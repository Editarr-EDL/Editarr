import assert from "node:assert/strict";
import { accountMenuButton } from "./account-menu.mjs";
import { chooseSeverity, defaultFiltersHeading, filtersTab, modeRadio, myFiltersHeading, newAccountModeHeading, openFilters, saveFiltersButton, severityChip } from "./filters-page.mjs";
import { randomBytes, createHash } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { capture } from "./capture.mjs";
import { verifySettingsWrites } from "./settings-write-probes.mjs";
import { SIGN_IN_PATH, admitJellyfinUser, fillSignIn } from "./run-jellyfin.mjs";

/** Prove settings permissions through real accounts, requests, and browser controls. */
export async function verifySettings(run, owner) {
  const { expect, request } = createRequire(path.join(run.root, "web/package.json"))("@playwright/test");
  const { default: AxeBuilder } = createRequire(path.join(run.root, "web/package.json"))("@axe-core/playwright");
  const origin = run.manifest.origin;
  const steps = [], requests = [], accessibility = [], failures = [];
  const contexts = [];
  const secret = () => { const value = randomBytes(24).toString("base64url"); run.secrets.add(value); return value; };
  const step = (name, detail = {}) => { steps.push({ name, ...detail }); run.write("settings-steps.json", steps); };
  const clients = [];

  async function client(account) {
    const api = await request.newContext({ baseURL: origin, extraHTTPHeaders: { Origin: origin } });
    clients.push(api);
    const login = await api.post(SIGN_IN_PATH, { data: { username: account.username, password: account.password } });
    assert.equal(login.status(), 200);
    const csrf = (await login.json()).csrf_token;
    run.secrets.add(csrf);
    return async (method, url, data) => {
      const response = await api.fetch(url, { method, data, headers: method === "GET" ? {} : { "X-CSRF-Token": csrf } });
      requests.push({ actor: account.username, method, path: url, status: response.status() });
      return { status: response.status(), body: await response.json().catch(() => null), response };
    };
  }

  const ownerApi = await client(owner);
  const fresh = { reauthentication: { password: owner.password } };
  async function create(username, role) {
    const account = await admitJellyfinUser(run, username);
    if (role === "admin") assert.equal((await ownerApi("PUT", `/api/users/${account.id}/role`, { role, ...fresh })).status, 200);
    return account;
  }

  async function open(account, width, section) {
    const context = await run.browser.newContext({ viewport: { width, height: 900 } });
    contexts.push(context);
    await context.route("**/*", route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort("blockedbyclient"));
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on("pageerror", error => failures.push(error.message));
    page.on("response", response => {
      const url = new URL(response.url());
      if (url.pathname.startsWith("/api/")) requests.push({ actor: account.username, width, method: response.request().method(), path: url.pathname, status: response.status() });
    });
    await page.goto(`${origin}/#/settings/${section}`);
    await fillSignIn(page, account.username, account.password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(accountMenuButton(page, account.username)).toBeVisible();
    return page;
  }

  async function proof(page, name) {
    await capture(page, run, name);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    const violations = result.violations.map(item => ({ id: item.id, nodes: item.nodes.length }));
    accessibility.push({ name, violations });
    run.write("settings-axe.json", accessibility);
    assert.deepEqual(violations, [], `${name} has axe violations`);
    await page.keyboard.press("Tab");
    assert.ok(await page.evaluate(() => document.activeElement !== document.body), `${name} has no keyboard focus target`);
  }

  try {
    const admin = await create("settings-admin", "admin");
    const user = await create("settings-user", "user");
    const operator = await create("settings-operator", "user");
    const adminApi = await client(admin), userApi = await client(user);
    const operatorApi = await client(operator);
    assert.equal((await ownerApi("PATCH", `/api/users/${operator.id}/permissions`, { overrides: {
      "media.paths.view": "allow", "path_mappings.test": "allow", "path_mappings.create": "allow", "path_mappings.view": "deny",
      "connections.view": "allow", "community.identity.view.own": "allow", "community.identity.enroll.own": "deny", "community.identity.profile.own": "deny",
      "community.identity.keys.own": "deny", "community.identity.disconnect.own": "deny",
    }, ...fresh })).status, 200);
    const mapping = await ownerApi("POST", "/api/settings/path-mappings", { remote_path: "/synthetic/remote", local_path: "/synthetic/local" });
    assert.equal(mapping.status, 200);
    const destination = path.join(run.scratch, "scheduled-backups");
    fs.mkdirSync(destination);
    step("Created synthetic accounts and one path mapping through production APIs");

    for (const [method, url, data] of [
      ["GET", "/api/models"], ["GET", "/api/settings/deployment"], ["GET", "/api/system/logs/settings"],
      ["GET", "/api/settings/path-mappings"], ["GET", "/api/backups/schedule"], ["POST", "/api/system/restart"],
      ["POST", "/api/models/faster-whisper-base/cancel"], ["DELETE", "/api/models/faster-whisper-base"],
      ["POST", "/api/backups/jobs", { action: "export", encrypt: false }],
    ]) {
      const denied = await userApi(method, url, data);
      assert.equal(denied.status, 403, `${method} ${url}`);
      assert.equal(denied.body.code, "capability_denied");
    }
    for (const [method, url, data] of [
      ["GET", "/api/settings/deployment"], ["PUT", "/api/settings/jellyfin", { server_url: "http://synthetic.test", api_key: "unused" }],
      ["POST", "/api/settings/path-mappings", { remote_path: "/denied", local_path: "/denied" }],
    ]) assert.equal((await adminApi(method, url, data)).status, 403, `${method} ${url}`);
    assert.equal((await adminApi("POST", "/api/backups/jobs", { action: "restore" })).status, 422);
    step("Ordinary-user operational calls and admin owner-only calls independently refused with 403");

    assert.equal((await operatorApi("GET", "/api/settings/path-mappings")).status, 403);
    assert.equal((await operatorApi("GET", "/api/community/connection/status")).status, 200);
    for (const width of [1440, 320]) {
      const page = await open(operator, width, "general");
      await expect(page.getByRole("table")).toHaveCount(0);
      await page.getByLabel("Test a remote path", { exact: true }).fill("/synthetic/remote/movie.mkv");
      await page.getByRole("button", { name: "Test resolution", exact: true }).click();
      await expect(page.getByText("Maps to /synthetic/local/movie.mkv", { exact: true })).toBeVisible();
      await page.getByLabel("Remote path", { exact: true }).fill(`/synthetic/operator-${width}`);
      await page.getByLabel("Local path", { exact: true }).fill(`/synthetic/local-${width}`);
      await page.getByRole("button", { name: "Add mapping", exact: true }).click();
      await expect(page.getByText("Path mapping added.", { exact: true })).toBeVisible();
      const listed = await ownerApi("GET", "/api/settings/path-mappings");
      assert.ok(listed.body.some(item => item.remote_path === `/synthetic/operator-${width}`));
      await proof(page, `settings-operator-mappings-without-list-${width}`);
      assert.deepEqual(requests.filter(item => item.actor === operator.username && item.width === width && item.method === "GET" && item.path === "/api/settings/path-mappings"), []);
      await page.goto(`${origin}/#/settings/connections`);
      const community = page.locator(".community-settings");
      await expect(community.getByRole("status").first()).toHaveText("Not connected");
      await expect(page.getByText("Path mapping added.", { exact: true })).toHaveCount(0);
      await expect(community.getByRole("button", { name: "Refresh connection", exact: true })).toBeEnabled();
      await expect(community.getByRole("button", { name: "Connect with Discord", exact: true })).toHaveCount(0);
      await community.getByRole("button", { name: "Refresh connection", exact: true }).click();
      await expect(community.getByRole("status").first()).toHaveText("Not connected");
      await proof(page, `settings-operator-community-readonly-${width}`);
      assert.deepEqual(requests.filter(item => item.actor === operator.username && item.width === width && item.method !== "GET" && item.path.startsWith("/api/community/")), []);
      step("Standalone mapping create/test work without list access; identity status remains readable without mutation grants", { width });
    }
    await verifySettingsWrites({ run, step, ownerApi, adminApi, operatorApi, admin, user, fresh });

    for (const width of [1440, 320]) {
      const userPage = await open(user, width, "general");
      await expect(userPage.getByRole("heading", { name: "About Editarr" })).toBeVisible();
      for (const heading of ["Deployment", "Diagnostics", "Base path mappings", "Backup and restore"]) await expect(userPage.getByRole("heading", { name: heading, exact: true })).toHaveCount(0);
      await proof(userPage, `settings-user-general-${width}`);
      await userPage.goto(`${origin}/#/settings/connections`);
      await expect(userPage.getByRole("heading", { name: "Page unavailable" })).toBeVisible();
      await expect(userPage.locator(".settings-page")).toHaveCount(0);
      await proof(userPage, `settings-user-denied-${width}`);
      await openFilters(userPage, origin);
      await expect(myFiltersHeading(userPage)).toBeVisible();
      await expect(filtersTab(userPage, "Server defaults")).toHaveCount(0);
      await expect(newAccountModeHeading(userPage)).toHaveCount(0);
      await expect(defaultFiltersHeading(userPage)).toHaveCount(0);
      await proof(userPage, `settings-user-filters-personal-${width}`);
      const catalogRead = userPage.waitForResponse(response => new URL(response.url()).pathname === "/api/capabilities");
      await userPage.goto(`${origin}/#/settings/models`);
      await expect(userPage.getByRole("heading", { name: "Download models" })).toBeVisible();
      const catalog = await catalogRead;
      run.write(`settings-catalog-${width}.json`, { status: catalog.status() });
      assert.equal(catalog.status(), 200, "an ordinary user reads the model catalog");
      const select = userPage.getByLabel("Model", { exact: true });
      await expect.poll(() => select.locator("option").count()).toBeGreaterThan(1);
      const modelId = await select.locator("option").nth(1).getAttribute("value");
      assert.ok(modelId);
      await select.selectOption(modelId);
      await expect(userPage.getByRole("button", { name: "Download", exact: true })).toBeEnabled();
      await expect(userPage.getByRole("button", { name: "Remove", exact: true })).toHaveCount(0);
      await proof(userPage, `settings-user-models-${width}`);
      const forbidden = requests.filter(item => item.actor === user.username && item.width === width && (item.path === "/api/models" || item.path.startsWith("/api/system/logs") || item.path.startsWith("/api/settings/deployment") || item.path.startsWith("/api/backups")));
      assert.deepEqual(forbidden, [], "ordinary UI fetched protected operational details");

      const adminPage = await open(admin, width, "connections");
      await expect(adminPage.getByRole("heading", { name: "Jellyfin", exact: true })).toBeVisible();
      await expect(adminPage.getByRole("heading", { name: "Community scans", exact: true })).toBeVisible();
      await expect(adminPage.getByLabel("API key", { exact: true })).toHaveCount(0);
      await expect(adminPage.getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
      await proof(adminPage, `settings-admin-connections-${width}`);
      await adminPage.goto(`${origin}/#/settings/general`);
      await expect(adminPage.getByRole("cell", { name: "/synthetic/remote", exact: true })).toBeVisible();
      await expect(adminPage.getByRole("heading", { name: "Deployment", exact: true })).toHaveCount(0);
      await expect(adminPage.getByRole("button", { name: "Add mapping", exact: true })).toHaveCount(0);
      await expect(adminPage.getByLabel("Backup file", { exact: true })).toHaveCount(0);
      await expect(adminPage.getByRole("button", { name: "Create backup", exact: true })).toBeVisible();
      await adminPage.getByLabel("Test a remote path", { exact: true }).fill("/synthetic/remote/movie.mkv");
      await adminPage.getByRole("button", { name: "Test resolution", exact: true }).click();
      await expect(adminPage.getByText("Maps to /synthetic/local/movie.mkv", { exact: true })).toBeVisible();
      await adminPage.getByRole("button", { name: "View logs", exact: true }).click();
      const debug = adminPage.getByLabel("Enable debug logging", { exact: true });
      const currentDebug = (await adminApi("GET", "/api/system/logs/settings")).body.debug_enabled;
      await expect(debug).toBeChecked({ checked: currentDebug });
      await expect(debug).toBeEnabled();
      if (currentDebug !== (width === 1440)) await debug.click();
      await expect.poll(async () => (await adminApi("GET", "/api/system/logs/settings")).body.debug_enabled).toBe(width === 1440);
      await adminPage.getByLabel("Interval (hours)", { exact: true }).fill("1");
      await adminPage.getByLabel("Backups to keep", { exact: true }).fill("2");
      await adminPage.getByLabel("Backup destination", { exact: true }).fill(destination);
      await expect(adminPage.getByLabel("Enable scheduled backups", { exact: true })).not.toBeChecked();
      await adminPage.getByRole("button", { name: "Save schedule", exact: true }).click();
      await expect.poll(async () => (await adminApi("GET", "/api/backups/schedule")).body.config).toMatchObject({ enabled: false, destination, interval_hours: 1, keep_count: 2 });
      await proof(adminPage, `settings-admin-general-${width}`);
      await adminPage.goto(`${origin}/#/settings/models`);
      await expect(adminPage.locator(".model-artifact").first()).toBeVisible();
      await proof(adminPage, `settings-admin-models-${width}`);
      await openFilters(adminPage, origin, "server");
      await expect(modeRadio(adminPage, "server", "live")).toBeEnabled();
      await expect(adminPage.getByRole("button", { name: "Add rule", exact: true })).toHaveCount(0);
      await modeRadio(adminPage, "server", width === 1440 ? "copy" : "live").check();
      await expect.poll(async () => (await adminApi("GET", "/api/settings/filters/default-mode")).body.mode).toBe(width === 1440 ? "copy" : "live");
      await proof(adminPage, `settings-admin-filters-${width}`);

      const ownerPage = await open(owner, width, "general");
      await expect(ownerPage.getByRole("heading", { name: "Deployment", exact: true })).toBeVisible();
      await expect(ownerPage.getByLabel("Backup file", { exact: true })).toHaveCount(0);
      await expect(ownerPage.getByRole("button", { name: "Add mapping", exact: true })).toBeVisible();
      await proof(ownerPage, `settings-owner-general-${width}`);
      await ownerPage.goto(`${origin}/#/settings/connections`);
      await expect(ownerPage.getByLabel("API key", { exact: true })).toBeVisible();
      for (const name of ["Test connection", "Save"]) await expect(ownerPage.getByRole("button", { name, exact: true })).toBeVisible();
      await expect(ownerPage.getByText(/Radarr|Sonarr/)).toHaveCount(0);
      await proof(ownerPage, `settings-owner-connections-${width}`);
      step("Owner/admin/user settings and denied deep links match the matrix", { width });
    }

    assert.equal((await ownerApi("PATCH", `/api/users/${admin.id}/permissions`, { overrides: { "deployment.view": "allow" }, ...fresh })).status, 200);
    const deployment = await adminApi("GET", "/api/settings/deployment");
    assert.equal(deployment.status, 200);
    assert.equal((await adminApi("PUT", "/api/settings/deployment", { port: deployment.body.saved.port })).status, 403);
    for (const width of [1440, 320]) {
      const page = await open(admin, width, "general");
      const panel = page.locator(".deployment-settings");
      await expect(panel.getByRole("region", { name: "Active now", exact: true })).toContainText(deployment.body.active.base_url);
      await expect(panel.getByRole("region", { name: "Next start", exact: true })).toContainText(String(deployment.body.next_start.listening_port));
      await expect(panel.locator("form, input")).toHaveCount(0);
      await expect(panel.getByRole("button", { name: "Save deployment settings", exact: true })).toHaveCount(0);
      await proof(page, `settings-admin-deployment-readonly-${width}`);
      step("Admin deployment.view grant exposes readable snapshots without deployment.edit controls; the real PUT remains forbidden", { width });
    }

    const operatorPage = await open(admin, 1440, "general");
    const before = await adminApi("GET", "/api/system/restart");
    assert.equal(before.status, 200);
    assert.equal(before.body.supported, true, "Use --controller for the real successful restart proof");
    // The page reloads itself once it observes the applied generation; capture only the fresh document.
    await Promise.all([
      operatorPage.waitForEvent("load", { timeout: 60000 }),
      (async () => {
        await operatorPage.getByRole("button", { name: "Restart Editarr", exact: true }).click();
        await expect.poll(async () => {
          try { return (await adminApi("GET", "/api/system/restart")).body; } catch { return null; }
        }, { timeout: 45000 }).toMatchObject({ generation: before.body.generation + 1, outcome: "applied" });
      })(),
    ]);
    assert.equal(await operatorPage.evaluate(() => performance.getEntriesByType("navigation")[0].type), "reload");
    await expect(operatorPage.getByRole("heading", { name: "General", exact: true })).toBeVisible();
    await expect(operatorPage.getByRole("button", { name: "Restart Editarr", exact: true })).toBeEnabled();
    await proof(operatorPage, "settings-admin-restarted");
    step("Admin restarted the real controller and independently observed the applied next generation");
    assert.equal((await adminApi("POST", "/api/models/faster-whisper-base/cancel")).status, 200);
    const cache = process.platform === "darwin"
      ? path.join(run.scratch, "home/Library/Caches/Editarr/models")
      : path.join(run.scratch, "cache/editarr/models");
    const artifact = path.join(cache, "artifacts/faster-whisper-base");
    const cancellation = path.join(cache, ".cancel/faster-whisper-base");
    assert.ok(fs.existsSync(cancellation), "Cancellation records a managed control file");
    const existed = fs.existsSync(artifact);
    const removed = await adminApi("DELETE", "/api/models/faster-whisper-base");
    assert.equal(removed.status, 200);
    assert.equal(removed.body.removed, existed, "removed reports a managed artifact directory, not downloaded weights");
    assert.equal(fs.existsSync(artifact), false);
    assert.equal(fs.existsSync(cancellation), false);
    const repeated = await adminApi("DELETE", "/api/models/faster-whisper-base");
    assert.equal(repeated.status, 200);
    assert.equal(repeated.body.removed, false);
    run.write("settings-model-removal.json", { artifactDirectoryExisted: existed, removed: removed.body.removed, repeatedRemoved: repeated.body.removed, cancellationCleared: true });
    const noWeights = await userApi("POST", "/api/models/prefetch", { backend_id: "better_profanity" });
    assert.equal(noWeights.status, 202);
    assert.equal(noWeights.body.status, null);
    step("Permitted model cancel/delete reached the real service; bundled acquisition returned nullable status without downloading weights");

    for (const width of [1440, 320]) {
      const ownerPage = await open(owner, width, "general");
      await expect(ownerPage.getByLabel("Backup contents", { exact: true })).toHaveCount(0);
      await ownerPage.getByRole("button", { name: "Create backup", exact: true }).click();
      const [download] = await Promise.all([ownerPage.waitForEvent("download"), ownerPage.getByRole("button", { name: "Download backup", exact: true }).click()]);
      const archive = path.join(run.scratch, `full-${width}.backup`);
      await download.saveAs(archive);
      assert.equal(await download.failure(), null);
      const bytes = fs.readFileSync(archive);
      assert.ok(bytes.length > 0);
      run.write(`full-archive-${width}.json`, { bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"), retained: false });
      for (const control of ["Backup file", "Account password"]) await expect(ownerPage.getByLabel(control, { exact: true })).toHaveCount(0);
      await expect(ownerPage.getByRole("button", { name: "Inspect backup", exact: true })).toHaveCount(0);
      await proof(ownerPage, `settings-owner-backup-${width}`);
      step("Owner downloaded a real full archive; Settings offers no restore or inspection", { width });
    }

    for (const width of [1440, 320]) {
      const defaults = (await ownerApi("GET", "/api/settings/filters")).body;
      const preferences = (await ownerApi("GET", `/api/settings/filters/users/${user.id}`)).body;
      assert.equal((await ownerApi("PUT", `/api/settings/filters/users/${user.id}/policy`, { policy_revision: preferences.policy_revision, rules: [], severities: ["swearing"], ...fresh })).status, 200);
      const ownerPage = await open(owner, width, "filters/server");
      const severity = severityChip(ownerPage, "Swearing");
      await expect(defaultFiltersHeading(ownerPage)).toBeVisible();
      await ownerPage.waitForLoadState("networkidle");
      await expect(ownerPage.getByText("Loading filter defaults…", { exact: true })).toHaveCount(0);
      const previous = defaults.severity_defaults.find(item => item.subcategory === "swearing").severity;
      const next = previous === "high" ? "low" : "high";
      const saveChanges = saveFiltersButton(ownerPage);
      await expect(severity).toHaveAccessibleName(`Swearing: ${previous[0].toUpperCase()}${previous.slice(1)}`);
      await expect(saveChanges).toBeDisabled();
      run.write(`settings-filter-ready-${width}.json`, {
        revision: defaults.revision, severity: previous,
        filterReads: requests.filter(item => item.actor === owner.username && item.width === width && item.method === "GET" && item.path === "/api/settings/filters").length,
        registryReads: requests.filter(item => item.actor === owner.username && item.width === width && item.method === "GET" && item.path === "/api/mcf-registry").length,
      });
      await chooseSeverity(ownerPage, "Swearing", next);
      await expect(severity).toHaveAccessibleName(`Swearing: ${next[0].toUpperCase()}${next.slice(1)}`);
      await expect(saveChanges).toBeEnabled();
      await saveChanges.click();
      const confirmation = ownerPage.getByRole("dialog", { name: "Confirm enforced filter changes", exact: true });
      await expect(confirmation).toBeVisible();
      await expect(confirmation.getByLabel(/^(?:Current|Jellyfin) password$/)).toBeFocused();
      await confirmation.getByLabel(/^(?:Current|Jellyfin) password$/).fill(secret());
      try {
        const refused = ownerPage.waitForResponse(response => new URL(response.url()).pathname === "/api/settings/filters" && response.request().method() === "PUT");
        await confirmation.getByRole("button", { name: "Save enforced changes", exact: true }).click();
        const response = await refused;
        assert.equal(response.status(), 403);
        assert.equal((await response.json()).code, "reauthentication_failed");
        await expect(confirmation.getByRole("alert")).toHaveText("That password is incorrect.");
      } catch (error) {
        await capture(ownerPage, run, `settings-enforced-password-failed-${width}`);
        throw error;
      }
      await expect(confirmation.getByLabel(/^(?:Current|Jellyfin) password$/)).toHaveValue("");
      await confirmation.getByLabel(/^(?:Current|Jellyfin) password$/).fill(owner.password);
      await confirmation.getByRole("button", { name: "Save enforced changes", exact: true }).click();
      await expect(ownerPage.getByText("Server defaults saved.", { exact: true })).toBeVisible();
      const saved = (await ownerApi("GET", "/api/settings/filters")).body;
      assert.equal(saved.revision, defaults.revision + 1);
      assert.notEqual(saved.severity_defaults.find(item => item.subcategory === "swearing").severity, previous);
      await proof(ownerPage, `settings-owner-enforced-save-${width}`);
      step("Owner enforced-default edit kept its draft through a wrong password then saved with request-scoped proof and a fresh revision read", { width });
    }

    assert.equal((await ownerApi("PATCH", `/api/users/${admin.id}/permissions`, { overrides: { "logs.settings.edit": "deny", "media.paths.view": "deny", "models.download": "deny", "models.download.cancel": "deny", "models.delete": "deny", "system.version.view": "deny" }, ...fresh })).status, 200);
    assert.equal((await adminApi("GET", "/api/system/version")).status, 403);
    for (const width of [1440, 320]) {
      await operatorPage.setViewportSize({ width, height: 900 });
      await operatorPage.goto(`${origin}/#/settings/general`);
      await operatorPage.reload();
      await expect(operatorPage.getByRole("heading", { name: "About Editarr" })).toHaveCount(0);
      await expect(operatorPage.getByRole("heading", { name: "Base path mappings" })).toHaveCount(0);
      await operatorPage.getByRole("button", { name: "Logging settings", exact: true }).click();
      await expect(operatorPage.getByLabel("Enable debug logging", { exact: true })).toHaveCount(0);
      await expect(operatorPage.getByLabel("Log source", { exact: true })).toHaveCount(0);
      await proof(operatorPage, `settings-admin-overrides-general-${width}`);
      await operatorPage.goto(`${origin}/#/settings/models`);
      await expect(operatorPage.locator(".model-artifact").first()).toBeVisible();
      for (const name of ["Download", "Cancel download", "Remove"]) await expect(operatorPage.getByRole("button", { name, exact: true })).toHaveCount(0);
      await proof(operatorPage, `settings-admin-overrides-models-${width}`);
      step("Live deny overrides removed path, version, log mutation, and model actions without removing safe inventory", { width });
    }
    assert.deepEqual(failures, [], "Browser raised an uncaught error");
    run.write("settings-result.json", { passed: true, feature: "mu-27", viewports: [1440, 320], realControllerRestart: true, archiveActivated: false, modelWeightsDownloaded: false, browserErrors: failures, steps: steps.length });
  } finally {
    run.write("settings-http-statuses.json", requests);
    run.write("settings-browser-errors.json", failures);
    for (const context of contexts) {
      for (const cookie of await context.cookies().catch(() => [])) run.secrets.add(cookie.value);
      await context.close();
    }
    for (const api of clients) {
      for (const cookie of (await api.storageState()).cookies) run.secrets.add(cookie.value);
      await api.dispose();
    }
  }
}