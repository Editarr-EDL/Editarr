import { expect, test } from "@playwright/test";
import { expectAccessible, expectNoOverflow, mockOperatorApi, strictOperatorPage, watchCsp } from "./operator-mock";

const pages: [string, string, RegExp][] = [["Home", "#/home", /Good (morning|afternoon|evening)/], ["Items", "#/items", /^Items$/],
  ["Activity", "#/activity", /^Activity$/], ["Contributors", "#/contributors", /^Contributors$/], ["Queue", "#/queue", /^Queue$/],
  ["Reports", "#/queue/reports", /^Reports$/], ["Alias suggestions", "#/queue/aliases", /^Alias suggestions$/],
  ["Candidate versions", "#/queue/versions", /^Candidate versions$/], ["Quarantined", "#/queue/quarantined", /^Quarantined$/],
  ["All versions", "#/versions", /^All versions$/], ["All alias suggestions", "#/aliases", /^All alias suggestions$/],
  ["All reports", "#/reports", /^All reports$/], ["Backups", "#/backups", /^Backups$/], ["Health & tools", "#/health", /^Health & tools$/], ["Audit log", "#/audit", /^Audit log$/]];

for (const width of [320, 1440]) {
  test.describe(`operator pages ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });
    test("sidebar navigation reaches every page with back/forward, no overflow, strict CSP and no axe violations", async ({ page }) => {
      await strictOperatorPage(page); await mockOperatorApi(page);
      const csp = await watchCsp(page);
      await page.goto("/admin/#/home");
      for (const [name, hash, heading] of pages) {
        if (width < 900) await page.getByRole("button", { name: "Open navigation" }).click();
        await page.getByRole("navigation", { name: "Operator pages" }).getByRole("link", { name: new RegExp(`^${name.replace("&", "\\&")}`) }).click();
        await expect(page).toHaveURL(new RegExp(`${hash.replace("?", "\\?")}$`));
        await expect(page.getByRole("heading", { level: 1 })).toHaveText(heading);
        await expect(page.getByRole("status").filter({ hasText: "Service snapshot loaded" }).first()).toBeAttached();
        await expectNoOverflow(page, width);
        await expectAccessible(page);
      }
      await page.goBack(); await expect(page.getByRole("heading", { level: 1 })).toHaveText(/^Health & tools$/);
      await page.goBack(); await expect(page.getByRole("heading", { level: 1 })).toHaveText(/^Backups$/);
      await page.goForward(); await expect(page.getByRole("heading", { level: 1 })).toHaveText(/^Health & tools$/);
      expect(csp.violations).toEqual([]);
      expect(await csp.inlineStyles()).toBe(0);
    });
    test("home summarises KPIs, decisions, updates, leaders and health", async ({ page }) => {
      await mockOperatorApi(page);
      await page.goto("/admin/#/home");
      await expect(page.getByText("5 decisions are waiting", { exact: false })).toBeVisible();
      await expect(page.locator(".kpi").filter({ hasText: "Votes" })).toContainText("1,284");
      await expect(page.locator("section[aria-labelledby=home-attention] .tt").filter({ hasText: /Report · Inaccurate — Violence › Gore/ })).toBeVisible();
      await expect(page.locator("section[aria-labelledby=home-top]").getByRole("link", { name: /Mira Okafor/ })).toBeVisible();
      await page.getByRole("button", { name: "30 days" }).click();
      await expect(page).toHaveURL(/#\/home\?period=30d$/);
      await page.goBack(); await expect(page).toHaveURL(/#\/home$/);
      await expect(page.getByRole("button", { name: "7 days" })).toHaveAttribute("aria-pressed", "true");
    });
    test("item detail selects a filter from the table, shows its card and excludes a vote with a reason", async ({ page }) => {
      const writes: Record<string, unknown>[] = [];
      await mockOperatorApi(page, (path, method, body) => {
        if (method === "POST") { const intent = body as Record<string, unknown>; writes.push(intent);
          return { version: 1, actionId: intent.actionId, resource: `vote:${intent.targetId}:${intent.contributorId}`, revision: 1, outcome: "applied" }; }
        if (path.startsWith("/admin/v1/moderation/vote/")) { const [, , , , , id, contributorId] = path.split("/");
          return { version: 1, resource: `vote:${id}:${contributorId}`, revision: 0, state: { filterId: id, contributorId, vote: "down", excluded: 0, updatedAt: 1 }, audit: [] }; }
        return undefined;
      });
      await page.goto("/admin/#/items/w_arrival");
      await expect(page.getByRole("heading", { level: 1 })).toContainText("Arrival Road");
      await expect(page.getByRole("button", { name: /Director's Cut/ })).toHaveAttribute("aria-pressed", "true");
      await expect(page.getByRole("heading", { name: "No filter selected" })).toBeVisible();
      await page.getByRole("table", { name: "Master filters" }).getByRole("link", { name: "Violence › Gore" }).click();
      await expect(page).toHaveURL(/filter=f_gore/);
      const card = page.getByRole("complementary", { name: "Violence › Gore" });
      await expect(card.getByText("Ends about four seconds early", { exact: false })).toBeVisible();
      await expect(card.getByText("3 of 4 counted")).toBeVisible();
      await expect(page.getByRole("img", { name: /./ }).or(page.locator("svg.track")).first()).toBeVisible();
      await card.getByRole("button", { name: "Exclude Ana Ruiz's down vote" }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("Operator reason").fill("Coordinated down-voting");
      await expectAccessible(page);
      await dialog.getByRole("button", { name: "Confirm action" }).click(); await expect(dialog).toHaveCount(0);
      expect(writes).toMatchObject([{ command: "exclude_vote", resource: "vote", targetId: "f_gore", contributorId: "c-ana", expectedRevision: 0 }]);
      await page.getByRole("button", { name: /Theatrical/ }).click();
      await expect(page).toHaveURL(/version=v_arrival_th/);
      await expectNoOverflow(page, width);
    });
    test("queue resolves a report with a reason, sends the listed revision and refreshes", async ({ page }) => {
      const writes: Record<string, unknown>[] = [];
      let queueReads = 0;
      await mockOperatorApi(page, (path, method, body) => {
        if (method === "POST") { const intent = body as Record<string, unknown>; writes.push(intent);
          return { version: 1, actionId: intent.actionId, resource: `report:${intent.targetId}`, revision: 1, outcome: "applied" }; }
        if (path.startsWith("/admin/v1/queue")) queueReads += 1;
        if (path.startsWith("/admin/v1/moderation/report/"))
          return { version: 1, resource: "report:r_gore", revision: 0, state: { reportId: "r_gore", contributorId: "c-ana", targetType: "filter", targetId: "f_gore",
            reason: "inaccurate", note: "Ends early", createdAt: 1, state: "open" }, audit: [] };
        return undefined;
      });
      await page.goto("/admin/#/queue/reports");
      const card = page.getByRole("article", { name: /Report · Inaccurate — Violence › Gore/ });
      await card.getByRole("button", { name: /^Resolve/ }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog.getByRole("heading", { name: "Resolve report" })).toBeVisible();
      await expect(dialog.getByRole("button", { name: "Confirm action" })).toBeDisabled();
      await dialog.getByLabel("Operator reason").fill("Retimed by the uploader; report addressed");
      await expectAccessible(page);
      const before = queueReads;
      await dialog.getByRole("button", { name: "Confirm action" }).click(); await expect(dialog).toHaveCount(0);
      await expect(page.getByRole("status").filter({ hasText: /Action applied\. Audit ID/ })).toBeVisible();
      expect(writes).toMatchObject([{ command: "resolve_report", resource: "report", targetId: "r_gore", expectedRevision: 0,
        reason: "Retimed by the uploader; report addressed" }]);
      await expect.poll(() => queueReads).toBeGreaterThan(before);
      await expectNoOverflow(page, width);
    });
    test("backups validate and save the schedule, start a run and stage a restore", async ({ page }) => {
      const requests: { method: string; path: string; body: unknown }[] = [];
      await mockOperatorApi(page, (path, method, body) => {
        if (method === "GET") return undefined;
        requests.push({ method, path, body });
        if (path.endsWith("/run")) return { status: 202, json: { version: 1, observedAt: 1, runId: "bk-28", status: "running" } };
        if (path.endsWith("/stage")) return { version: 1, observedAt: 1, runId: "bk-27", stagedPath: "/s/bk-27.sqlite", sha256: "abc123", bytes: 1,
          schemaVersion: 5, restoreVolume: "restore-bk-27", commands: ["docker compose stop community", "editarr-community restore --apply restore-bk-27"] };
        return undefined;
      });
      await page.goto("/admin/#/backups");
      const time = page.getByLabel("Time (UTC)");
      await time.fill("25:00");
      await expect(page.getByText("Use 24-hour HH:MM", { exact: false })).toBeVisible();
      await expect(page.getByRole("button", { name: "Save schedule" })).toBeDisabled();
      await time.fill("04:15");
      await page.getByLabel("Keep last").fill("7");
      await expectAccessible(page);
      await page.getByRole("button", { name: "Save schedule" }).click();
      await expect(page.getByText("Schedule saved and audited.")).toBeVisible();
      expect(requests[0]).toMatchObject({ method: "PUT", path: "/admin/v1/backups/config", body: { enabled: true, frequency: "daily", atUtc: "04:15",
        keepLast: 7, keepWeekly: 8, destination: "nightly", integrityCheck: true } });
      await page.getByRole("button", { name: "Run backup now" }).click();
      await expect(page.getByText("Backup started.", { exact: false })).toBeVisible();
      expect(requests[1]).toMatchObject({ method: "POST", path: "/admin/v1/backups/run", body: null });
      await page.getByRole("button", { name: "Stage for restore" }).click();
      await expect(page.getByText("editarr-community restore --apply restore-bk-27")).toBeVisible();
      const restore = page.getByRole("region", { name: "Restore a backup" }).or(page.locator("section[aria-labelledby=restore-title]"));
      await expect(restore).toContainText("refuses an existing database");
      await expect(restore).toContainText("name: restore-bk-27");
      await expect(restore).not.toContainText("rollback copy");
      await expect(page.getByRole("link", { name: /^Download/ }).first()).toHaveAttribute("href", "/admin/v1/backups/bk-27/download");
      await expectNoOverflow(page, width);
    });
    test("health trend charts have text equivalents and the period is routed", async ({ page }) => {
      await mockOperatorApi(page);
      await page.goto("/admin/#/health");
      for (const name of [/Database size over 30 d/, /Stored upload payloads over 30 d/, /Rate-limit denials over 30 d/, /Requests over 30 d/,
        /Uploads per bucket/, /Votes per bucket/, /Master downloads over 30 d/])
        await expect(page.getByRole("img", { name })).toHaveAttribute("aria-label", /lowest .+ highest .+ latest/);
      await expect(page.getByRole("table", { name: "Retention sweeps" }).getByText("Batch limit")).toBeVisible();
      await page.getByRole("button", { name: "7 days" }).click();
      await expect(page.getByRole("img", { name: /Database size over 7 d/ })).toBeVisible();
      await expect(page).toHaveURL(/period=7d/);
      await expectAccessible(page);
    });
  });
}

test("legacy hashes land on the matching views", async ({ page }) => {
  await mockOperatorApi(page);
  for (const [hash, heading] of [["#/versions?filter=candidate", "All versions"], ["#/aliases?filter=all", "All alias suggestions"], ["#/reports", "All reports"],
    ["#/contributors", "Contributors"], ["#/operations", "Health & tools"], ["#/audit", "Audit log"], ["#/backup", "Backups"]] as const) {
    await page.goto(`/admin/${hash}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(heading);
  }
  await page.goto("/admin/#/operations");
  await expect(page.getByRole("table", { name: "Operations log" })).toContainText("Publication");
  await expect(page.getByRole("table", { name: "Daily handler observations" })).toBeVisible();
  await page.goto("/admin/#/contributors?tab=all");
  await expect(page.getByRole("table", { name: "All contributors" })).toBeVisible();
  await expectAccessible(page);
});

for (const width of [320, 390]) {
  test(`drawer opens, traps the page and closes at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await mockOperatorApi(page);
    await page.goto("/admin/#/home");
    const nav = page.getByRole("navigation", { name: "Operator pages" });
    await expect(nav.getByRole("link", { name: "Items" })).not.toBeInViewport();
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(nav.getByRole("link", { name: "Items" })).toBeInViewport();
    await expect(page.getByRole("button", { name: "Open navigation" })).toHaveAttribute("aria-expanded", "true");
    await expectAccessible(page);
    await page.keyboard.press("Escape");
    await expect(nav.getByRole("link", { name: "Items" })).not.toBeInViewport();
    await page.getByRole("button", { name: "Open navigation" }).click();
    await nav.getByRole("link", { name: /^Queue/ }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Queue");
    await expect(nav.getByRole("link", { name: "Items" })).not.toBeInViewport();
    await expectNoOverflow(page, width);
  });
}

test("global search finds items, contributors and IDs", async ({ page }) => {
  await mockOperatorApi(page);
  await page.goto("/admin/#/home");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Good");
  const search = page.getByLabel("Search titles, contributors or any ID");
  await expect(async () => { await page.keyboard.press("/"); await expect(search).toBeFocused({ timeout: 500 }); }).toPass();
  await search.fill("arrival");
  const results = page.getByRole("region", { name: "Search results" });
  await expect(results.getByRole("link", { name: "Arrival Road (1977)", exact: true })).toBeVisible();
  await expect(results.getByRole("link", { name: "Ana Ruiz" })).toBeVisible();
  await results.getByRole("link", { name: /Filter f_gore/ }).click();
  await expect(page).toHaveURL(/#\/items\/w_arrival\?version=v_arrival_dc&filter=f_gore$/);
});
