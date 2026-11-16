import assert from "node:assert/strict";
import { accountMenuButton, signOut } from "./account-menu.mjs";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { capture } from "./capture.mjs";
import { childEnvironment, pythonPath } from "./prerequisites.mjs";
import { SIGN_IN_PATH, admitJellyfinUser, fillSignIn } from "./run-jellyfin.mjs";

/** Exercise audit scope and retention through the real source app with disposable accounts. */
export async function verifyAuditHistory(run, owner, { injectFailure = false } = {}) {
  const load = createRequire(path.join(run.root, "web/package.json"));
  const { expect, request } = load("@playwright/test");
  const { default: AxeBuilder } = load("@axe-core/playwright");
  const origin = run.manifest.origin;
  const contexts = [];
  const steps = [];
  const http = [];
  const failures = [];
  const axe = [];
  let lastPage;
  const step = name => { steps.push(name); run.write("audit-steps.json", steps); };
  const remember = async context => {
    const state = await context.storageState();
    for (const cookie of state.cookies) run.secrets.add(cookie.value);
  };
  async function api(username, password) {
    const context = await request.newContext({ baseURL: origin, extraHTTPHeaders: { Origin: origin } });
    contexts.push(context);
    const login = await context.post(SIGN_IN_PATH, { data: { username, password } });
    assert.equal(login.status(), 200);
    const csrf = (await login.json()).csrf_token;
    run.secrets.add(csrf);
    await remember(context);
    return async (method, url, data) => {
      const response = await context.fetch(url, { method, data, headers: method === "GET" ? {} : { "X-CSRF-Token": csrf } });
      http.push({ method, path: url, status: response.status() });
      const body = await response.json().catch(() => null);
      if (body?.api_key) run.secrets.add(body.api_key);
      return { status: response.status(), body, headers: response.headers() };
    };
  }
  async function open(actor, width) {
    const context = await run.browser.newContext({ viewport: { width, height: 900 } });
    contexts.push(context);
    await context.route("**/*", route => {
      const url = new URL(route.request().url());
      if (["http:", "https:"].includes(url.protocol) && url.origin !== origin) {
        failures.push("Unexpected external browser request");
        return route.abort("blockedbyclient");
      }
      return route.continue();
    });
    const page = await context.newPage();
    lastPage = page;
    page.setDefaultTimeout(20000);
    page.on("pageerror", error => failures.push(error.message));
    const reads = [];
    page.on("request", item => { if (new URL(item.url()).pathname.startsWith("/api/audit")) reads.push(new URL(item.url()).pathname); });
    await page.goto(`${origin}/#/audit`);
    await signIn(page, actor);
    await remember(context);
    return { page, context, reads };
  }
  async function signIn(page, actor) {
    await fillSignIn(page, actor.username, actor.password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(accountMenuButton(page, actor.username)).toBeVisible();
  }
  async function inspect(page, name) {
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    axe.push({ name, violations: result.violations.map(item => item.id) });
    assert.deepEqual(result.violations.map(item => item.id), [], name);
    await capture(page, run, name);
  }
  try {
    const ownerApi = await api(owner.username, owner.password);
    const fresh = { reauthentication: { password: owner.password } };
    async function create(username, role) {
      const account = await admitJellyfinUser(run, username);
      if (role === "admin") assert.equal((await ownerApi("PUT", `/api/users/${account.id}/role`, { role, ...fresh })).status, 200);
      return account;
    }
    const admin = await create("audit-admin", "admin");
    const member = await create("audit-member", "user");
    const adminApi = await api(admin.username, admin.password);
    const memberApi = await api(member.username, member.password);
    const ownerStatus = await ownerApi("GET", "/api/auth/status");
    const ownerId = ownerStatus.body.access.principal.user_id;
    run.secrets.add(ownerStatus.body.csrf_token);
    for (const id of [ownerId, member.id]) assert.equal((await ownerApi("POST", `/api/users/${id}/api-key`, fresh)).status, 200);
    const issued = await ownerApi("GET", "/api/audit?action=api_key.issue");
    assert.equal(issued.body.total, 2);
    const adminIssues = await adminApi("GET", "/api/audit?action=api_key.issue");
    assert.deepEqual([adminIssues.status, adminIssues.body.total, adminIssues.body.pages, adminIssues.body.events], [200, 0, 0, []]);
    for (const entry of issued.body.events) assert.equal((await adminApi("GET", `/api/audit/${entry.event_id}`)).status, 404);
    const permissions = await ownerApi("GET", "/api/audit?action=permissions.change");
    assert.ok(permissions.body.total > 0);
    assert.equal((await adminApi("GET", "/api/audit?action=permissions.change")).body.total, 0);
    for (const entry of permissions.body.events) assert.equal((await adminApi("GET", `/api/audit/${entry.event_id}`)).status, 404);
    for (const id of [ownerId, member.id]) assert.equal((await ownerApi("DELETE", `/api/users/${id}/api-key`)).status, 200);
    const all = await ownerApi("GET", "/api/audit?action=api_key.revoke");
    assert.equal(all.status, 200);
    assert.equal(all.body.total, 2);
    assert.equal(all.headers["cache-control"], "private, no-store");
    const hidden = all.body.events.find(item => item.target_user_id === ownerId).event_id;
    const visible = all.body.events.find(item => item.target_user_id === member.id).event_id;
    const scoped = await adminApi("GET", "/api/audit?action=api_key.revoke&page_size=1");
    assert.deepEqual([scoped.status, scoped.body.total, scoped.body.pages, scoped.body.events[0].event_id], [200, 1, 1, visible]);
    const hiddenFilter = await adminApi("GET", `/api/audit?target_user_id=${ownerId}`);
    assert.deepEqual([hiddenFilter.body.total, hiddenFilter.body.pages, hiddenFilter.body.events], [0, 0, []]);
    for (const id of [hidden, "00000000-0000-0000-0000-000000000000"]) assert.equal((await adminApi("GET", `/api/audit/${id}`)).status, 404);
    assert.equal((await memberApi("GET", "/api/audit")).status, 403);
    assert.equal((await memberApi("GET", `/api/audit/${visible}`)).status, 403);
    for (const value of run.secrets) assert.ok(!JSON.stringify(all.body).includes(value), "An audit projection contains a run secret");
    step("owner-only key issuance and permission changes are hidden from admins; delegated revocation scope precedes totals, filters, pages, and detail; ordinary API refused");

    for (const width of [1440, 320]) {
      const { page, reads } = await open(member, width);
      await expect(page.getByRole("heading", { name: "Page unavailable", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "Audit history", exact: true })).toHaveCount(0);
      assert.deepEqual(reads, []);
      await inspect(page, `audit-user-denied-${width}`);
      const { page: adminPage } = await open(admin, width);
      await expect(adminPage.getByRole("heading", { name: "Audit history", exact: true })).toBeVisible();
      await adminPage.getByLabel("Action", { exact: true }).fill("api_key.revoke");
      await adminPage.getByRole("button", { name: "Apply filters", exact: true }).click();
      await expect(adminPage.getByText("1 event", { exact: true })).toBeVisible();
      await expect(adminPage.getByRole("link", { name: "api_key.revoke", exact: true })).toHaveCount(1);
      await adminPage.getByRole("button", { name: "Refresh", exact: true }).focus();
      await adminPage.keyboard.press("Tab");
      await expect(adminPage.getByLabel("Category", { exact: true })).toBeFocused();
      await inspect(adminPage, `audit-admin-list-${width}`);
      if (injectFailure) throw new Error("Injected audit journey failure after scoped UI read");
      await adminPage.getByRole("link", { name: "api_key.revoke", exact: true }).click();
      await expect(adminPage.getByRole("heading", { name: "api_key.revoke", exact: true })).toBeVisible();
      await inspect(adminPage, `audit-admin-detail-${width}`);
      const { page: ownerPage } = await open(owner, width);
      await expect(ownerPage.getByRole("heading", { name: "Audit history", exact: true })).toBeVisible();
      await ownerPage.getByLabel("Action", { exact: true }).fill("api_key.revoke");
      await ownerPage.getByRole("button", { name: "Apply filters", exact: true }).click();
      await expect(ownerPage.getByText("2 events", { exact: true })).toBeVisible();
      await inspect(ownerPage, `audit-owner-list-${width}`);
    }
    step("owner, scoped admin, and denied ordinary UI passed at 1440 and 320 with keyboard, axe, and overflow evidence");

    assert.equal((await ownerApi("PATCH", `/api/users/${admin.id}/permissions`, { overrides: { "api_keys.revoke.other": "deny" }, ...fresh })).status, 200);
    const deniedAction = await adminApi("GET", "/api/audit?action=api_key.revoke");
    assert.deepEqual([deniedAction.body.total, deniedAction.body.pages, deniedAction.body.events], [0, 0, []]);
    assert.equal((await adminApi("GET", `/api/audit/${visible}`)).status, 404);
    assert.equal((await ownerApi("PATCH", `/api/users/${admin.id}/permissions`, { overrides: { "api_keys.revoke.other": "allow" }, ...fresh })).status, 200);
    assert.equal((await adminApi("GET", "/api/audit?action=api_key.revoke")).body.total, 1);
    assert.equal((await adminApi("GET", `/api/audit/${visible}`)).status, 200);
    step("live action-capability deny removes counts and details; allow restores visibility");

    const { page: changed } = await open(admin, 1440);
    await expect(changed.getByRole("heading", { name: "Audit history", exact: true })).toBeVisible();
    assert.equal((await ownerApi("PATCH", `/api/users/${admin.id}/permissions`, { overrides: { "audit.read": "deny" }, ...fresh })).status, 200);
    await changed.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(changed.getByRole("heading", { name: "Page unavailable", exact: true })).toBeVisible();
    await expect(changed.getByRole("button", { name: "Audit history", exact: true })).toHaveCount(0);
    await capture(changed, run, "audit-capability-removed");
    assert.equal((await ownerApi("PATCH", `/api/users/${admin.id}/permissions`, { overrides: { "audit.read": "inherit" }, ...fresh })).status, 200);
    step("capability removal refuses the existing browser and clears its audit view and navigation");

    const { page: retainedOwner, context: switched } = await open(owner, 1440);
    const { page: retainedAdmin } = await open(admin, 320);
    for (const page of [retainedOwner, retainedAdmin]) {
      await page.goto(`${origin}/#/audit/${visible}`);
      await expect(page.getByRole("heading", { name: "api_key.revoke", exact: true })).toBeVisible();
    }
    const retention = JSON.parse(execFileSync(pythonPath(run.root), [path.join(run.root, ".github/skills/verify-editarr/scripts/audit_retention.py"), run.data], {
      cwd: run.root, env: childEnvironment(run.root, run.data), encoding: "utf8",
    }));
    assert.ok(retention.deleted > 0);
    run.write("audit-retention.json", retention);
    for (const [page, name] of [[retainedOwner, "owner"], [retainedAdmin, "admin"]]) {
      await page.getByRole("button", { name: "Refresh", exact: true }).click();
      await expect(page.getByText(/Event no longer available/)).toBeVisible();
      await expect(page.getByRole("heading", { name: "api_key.revoke", exact: true })).toHaveCount(0);
      await page.getByRole("link", { name: "Back to audit history", exact: true }).click();
      await expect(page.getByText("No audit events in this selection.", { exact: true })).toBeVisible();
      await capture(page, run, `audit-retained-empty-${name}`);
    }
    assert.equal((await adminApi("GET", `/api/audit/${visible}`)).status, 404);
    await signOut(retainedOwner);
    await signIn(retainedOwner, member);
    await remember(switched);
    await retainedOwner.goto(`${origin}/#/audit/${visible}`);
    await expect(retainedOwner.getByRole("heading", { name: "Page unavailable", exact: true })).toBeVisible();
    await expect(retainedOwner.getByText("api_key.revoke", { exact: true })).toHaveCount(0);
    const stored = await retainedOwner.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
    assert.ok(!stored.includes(visible) && !stored.includes("api_key.revoke"));
    step("service retention removes the event; refresh clears both accounts; a same-context account switch carries no history");
    assert.deepEqual(failures, []);
    run.write("audit-result.json", { feature: "mu-audit-history", passed: true, viewports: [1440, 320],
      retentionBoundary: "run_retention(now=wall_clock+91days) on this run's disposable database only",
      scope: "Real authenticated source UI and API. Media scoping separately uses MU-11 decisions in test_audit_history_http.py. No external provider, native, installed, or performance certification." });
  } catch (error) {
    if (lastPage && !lastPage.isClosed()) await capture(lastPage, run, "audit-failure").catch(() => {});
    throw error;
  } finally {
    run.write("audit-http-statuses.json", http);
    run.write("audit-browser-errors.json", failures);
    run.write("audit-axe.json", axe);
    for (const context of contexts) {
      await remember(context).catch(() => {});
      if (context.close) await context.close(); else await context.dispose();
    }
  }
}
