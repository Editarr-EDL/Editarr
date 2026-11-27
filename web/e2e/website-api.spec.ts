import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const noHorizontalOverflow = () => document.documentElement.scrollWidth <= window.innerWidth;

test("API reference search, schemas, deep links, layout, and accessibility", async ({ page }, info) => {
  await page.goto("/api/");
  await expect(page).toHaveTitle("API reference — Editarr");
  await expect(page.locator('#navigation a[href="/api/"]')).toHaveAttribute("aria-current", "page");
  const count = page.getByRole("status");
  await expect(count).toContainText(/^\d+ of \d+ endpoints · API /);
  expect(await page.evaluate(noHorizontalOverflow)).toBe(true);
  expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
  await page.screenshot({ path: `test-results/editarr-api-${info.project.name}.png` });

  await page.getByLabel("Find an endpoint").fill("api/auth/api-key");
  const operations = page.locator(".api-op:visible > summary");
  await expect(operations.filter({ hasText: /^POST\s*\/api\/auth\/api-key\/reveal/ })).toBeVisible();
  await expect(operations.filter({ hasText: "/api/scans" })).toHaveCount(0);
  await operations.filter({ hasText: /^POST\s*\/api\/auth\/api-key\s*Own Issue/ }).click();
  const opened = page.locator(".api-op[open]");
  await expect(opened.locator(".api-block").filter({ hasText: "Request body" })).toContainText("FreshKeyRequest");
  await expect(opened.locator(".api-response").filter({ hasText: "200" })).toContainText("ApiKeySecret");
  await expect(opened.locator(".api-auth")).toHaveText("Browser session only");
  await expect(opened.locator(".api-example-note")).toContainText("API keys receive 403 interactive_session_required");
  await expect(opened.locator(".api-code")).toHaveCount(0);
  expect(await page.evaluate(noHorizontalOverflow)).toBe(true);
  expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
  await page.screenshot({ path: `test-results/editarr-api-${info.project.name}-open.png` });

  await page.getByLabel("Find an endpoint").fill("not-an-endpoint");
  await expect(count).toContainText("0 of");
});

test("API reference examples, enum parameters, and union branches follow the schema", async ({ page }) => {
  const open = async (id: string) => {
    await page.goto(`/api/#${id}`);
    const operation = page.locator(`#${id}`);
    await expect(operation).toHaveAttribute("open", "");
    return operation;
  };

  const keyed = await open("op-api_list_scan_history_api_scans_history_get");
  await expect(keyed.locator(".api-auth")).toHaveText("API key or browser session");
  await expect(keyed.locator(".api-code")).toContainText("X-Api-Key: $EDITARR_API_KEY");

  const upload = await open("op-api_import_edl_upload_api_edl_imports_upload_post");
  const curl = upload.locator(".api-code");
  await expect(curl).toContainText('-F "file=@path/to/file"');
  await expect(curl).toContainText('-F "options=<options.json"');
  await expect(curl).not.toContainText("options=@");

  const roles = await open("op-change_role_defaults_api_roles__role__defaults_patch");
  await expect(roles.locator(".api-type-cell").first()).toHaveText("admin | user");

  const filters = await open("op-put_own_api_settings_filters_personal_put");
  const body = filters.locator(".api-block").filter({ hasText: "Request body" });
  await expect(body.locator(".api-union-intro")).toContainText(/chosen by\s*mode/);
  const live = body.locator(".api-node").filter({ hasText: "LivePreferencesUpdate" }).first();
  await expect(live.locator("> summary")).toContainText('mode: "live"');
  await live.locator("> summary").click();
  await expect(live.locator(".api-props .api-prop-name").first()).toBeVisible();
  await expect(body.locator(".api-node").filter({ hasText: "CopyPreferencesUpdate" }).first()).toContainText('mode: "copy"');

  const scan = await open("op-api_enqueue_scan_api_scans_post");
  await expect(scan.locator(".api-code")).toContainText('"scan_audio":false,"scan_video":true');
  const schedule = await open("op-configure_schedule_api_backups_schedule_put");
  await expect(schedule.locator(".api-code")).toContainText('"interval_hours":1,"keep_count":1');

  const logout = await open("op-auth_logout_api_auth_logout_post");
  await expect(logout.locator(".api-auth")).toHaveText("Browser session cookie only");
  await expect(logout.locator(".api-example-note")).toContainText("401 authentication_required");
  await expect(logout.locator(".api-example-note")).not.toContainText("interactive_session_required");
  await expect(logout.locator(".api-code")).toHaveCount(0);

  const restore = await open("op-begin_flow_api_setup_restore_flow_post");
  await expect(restore.locator(".api-auth")).toHaveText("First-run browser flow");
  await expect(restore.locator(".api-example-note")).toContainText("X-Setup-Restore-Token");
  await expect(restore.locator(".api-code")).toHaveCount(0);
});

test("API reference search waits for the OpenAPI document", async ({ page }) => {
  const errors: Error[] = [];
  page.on("pageerror", (error) => errors.push(error));
  let release = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/docs/openapi.json", async (route) => {
    await held;
    await route.continue();
  });
  await page.goto("/api/");
  const count = page.getByRole("status");
  const loading = await count.textContent();
  await page.getByLabel("Find an endpoint").fill("api-key");
  await page.waitForTimeout(200);
  await expect(count).toHaveText(loading ?? "");
  release();
  await expect(count).toContainText(/^\d+ of \d+ endpoints/);
  await expect(page.locator(".api-op:visible > summary").filter({ hasText: "/api/scans" })).toHaveCount(0);

  await page.unroute("**/docs/openapi.json");
  await page.route("**/docs/openapi.json", (route) => route.fulfill({ status: 503 }));
  await page.goto("/api/");
  await expect(count).toContainText("could not be loaded");
  await page.getByLabel("Find an endpoint").fill("api-key");
  await page.waitForTimeout(200);
  await expect(count).toContainText("could not be loaded");
  expect(errors).toEqual([]);
});

test("API reference deep links open and scroll to an operation", async ({ page }) => {
  await page.goto("/api/#op-own_issue_api_auth_api_key_post");
  const target = page.locator("#op-own_issue_api_auth_api_key_post");
  await expect(target).toHaveAttribute("open", "");
  await expect(target.locator("> summary")).toBeInViewport();
  await expect(target).toContainText("ApiKeySecret");

  await page.getByLabel("Find an endpoint").fill("not-an-endpoint");
  await page.goto("/api/#tag-backups-and-restore");
  await expect(page.locator("#tag-backups-and-restore h2")).toBeInViewport();
});
