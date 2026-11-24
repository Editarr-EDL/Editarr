import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { operatorFixture, contributorFixture, pageFixture, reportFixture } from "../src/settings/community-operator/test-fixtures";

for (const width of [320, 1440]) {
  test.describe(`operator access ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });
    test("verified identity, refresh, keyboard, image, responsive layout and access isolation", async ({ page }) => {
      const localRequests: string[] = [];
      page.on("request", (request) => {
        if (new URL(request.url()).pathname.startsWith("/api/")) localRequests.push(request.url());
      });
      let reads = 0;
      await page.route("**/admin/v1/session", (route) => {
        reads += 1;
        return route.fulfill({ json: { version: 1, subject: `${reads === 1 ? "first" : "fresh"}-${"operator-subject-".repeat(12)}` } });
      });
      await page.route("**/admin/", async (route) => {
        const response = await route.fetch();
        await route.fulfill({ response, headers: { ...response.headers(),
          "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'" } });
      });
      await page.goto("/admin/");
      await expect(page.getByRole("region", { name: "Access status" }).getByRole("status")).toHaveText("Access verified");
      const logo = page.locator("header img");
      expect(await logo.evaluate((image) => (image as HTMLImageElement).naturalWidth)).toBe(32);
      await page.keyboard.press("Tab");
      await expect(page.getByRole("button", { name: "Refresh access" })).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(page.getByRole("region", { name: "Access status" }).locator("dd")).toContainText("fresh-");
      expect(localRequests).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      expect(await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze().then((result) => result.violations)).toEqual([]);
      await page.screenshot({ path: `test-results/css55-verified-${width}.png`, fullPage: true });
    });
    for (const [status, message] of [[401, "Sign-in required"], [403, "Operator access denied"], [503, "Access verification unavailable"]] as const) {
      test(`denial ${status} has no verified identity or local sign-in`, async ({ page }) => {
        await page.route("**/admin/v1/session", (route) => route.fulfill({ status, json: { error: { code: "fixture-denial" } } }));
        await page.goto("/admin/");
        await expect(page.getByText(message, { exact: true })).toBeVisible();
        await expect(page.locator("dd")).toHaveCount(0);
        await expect(page.locator("input[type=password]")).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      });
    }
    test("offline and login HTML fail closed", async ({ page }) => {
      await page.route("**/admin/v1/session", (route) => route.abort("failed"));
      await page.goto("/admin/");
      await expect(page.getByRole("alert")).toHaveText("Access verification unavailable");
      await page.unroute("**/admin/v1/session");
      await page.route("**/admin/v1/session", (route) => route.fulfill({ contentType: "text/html", body: "<html>Login</html>" }));
      await page.getByRole("button", { name: "Refresh access" }).click();
      await expect(page.getByRole("alert")).toHaveText("Access verification unavailable");
      await expect(page.locator("dd")).toHaveCount(0);
    });
    test("dashboard navigation, escaped inspection, storage provenance and keyboard/mobile layout", async ({ page }) => {
      const requests: string[] = [];
      page.on("request", request => { if (new URL(request.url()).pathname.startsWith("/api/")) requests.push(request.url()); });
      await page.route("**/admin/v1/**", route => route.fulfill({ json: operatorFixture(new URL(route.request().url()).pathname) }));
      await page.goto("/admin/#/overview");
      await expect(page.getByText("Service snapshot loaded")).toBeVisible();
      await expect(page.getByRole("region", { name: "Payload storage" }).getByText("12,345", { exact: true })).toBeVisible();
      await expect(page.getByRole("region", { name: "Provider measurements" }).getByText("Unavailable", { exact: true })).toHaveCount(1);
      expect(await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze().then(result => result.violations)).toEqual([]);
      await page.screenshot({ path: `test-results/css57-overview-${width}.png`, fullPage: true });
      await page.getByRole("link", { name: "Contributors", exact: true }).focus();
      await page.keyboard.press("Enter");
      await page.getByRole("link", { name: "Inspect contributor-1", exact: true }).click();
      await expect(page.getByRole("region", { name: "Contributor usage" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Key history" }).getByText("retired", { exact: true })).toBeVisible();
      expect(await page.getByRole("region", { name: "Resource inspection" }).locator("img").count()).toBe(0);
      expect(await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze().then(result => result.violations)).toEqual([]);
      await page.screenshot({ path: `test-results/css57-contributor-${width}.png`, fullPage: true });
      await page.getByRole("link", { name: "Reports", exact: true }).click();
      await page.getByRole("link", { name: "Inspect report-1", exact: true }).click();
      await expect(page.getByRole("region", { name: "Resource inspection" }).getByText("<script>window.hostileTextExecuted=true</script>", { exact: true })).toBeVisible();
      expect(await page.evaluate(() => (window as Window & { hostileTextExecuted?: boolean }).hostileTextExecuted)).toBeUndefined();
      for (const name of ["Versions", "Alias suggestions", "Operations", "Audit history", "Resource lookup", "Recompute", "Backup status"]) {
        await page.getByRole("link", { name, exact: true }).click();
        await expect(page.getByText("Service snapshot loaded")).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      }
      await expect(page.getByRole("heading", { name: "Backup and recovery" })).toBeVisible();
      expect(requests).toEqual([]);
      expect(await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze().then(result => result.violations)).toEqual([]);
    });
    test("version inspection with master filters, submissions and catalog action dialogs", async ({ page }) => {
      await page.route("**/admin/v1/**", route => route.fulfill({ json: operatorFixture(new URL(route.request().url()).pathname) }));
      await page.goto("/admin/#/versions?selected=version-1");
      await expect(page.getByRole("region", { name: "Master filters", exact: true }).getByText("violence", { exact: true })).toBeVisible();
      await expect(page.getByRole("region", { name: "Submissions", exact: true })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      expect(await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze().then(result => result.violations)).toEqual([]);
      await page.screenshot({ path: `test-results/operator-version-${width}.png`, fullPage: true });
      await page.getByRole("button", { name: "Merge into another version", exact: true }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog.getByLabel("Target version ID (same work)")).toBeVisible();
      expect(await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze().then(result => result.violations)).toEqual([]);
      await page.screenshot({ path: `test-results/operator-merge-${width}.png` });
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await page.goto("/admin/#/aliases?selected=suggestion-1");
      await page.getByRole("button", { name: "Approve alias", exact: true }).click();
      await dialog.getByLabel("Create a new alias").check();
      await expect(dialog.getByLabel("New alias label")).toBeVisible();
      expect(await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze().then(result => result.violations)).toEqual([]);
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await page.goto("/admin/#/recompute");
      await expect(page.getByRole("button", { name: "Recompute all versions" })).toBeDisabled();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      expect(await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze().then(result => result.violations)).toEqual([]);
    });
    test("bounded pagination, stale refresh, expired-session recovery and missing resource", async ({ page }) => {
      let expired = false, unavailable = false;
      const rows = Array.from({ length: 20 }, (_, index) => ({ ...contributorFixture, contributorId: `contributor-${index}` }));
      const cursor = btoa('["contributor-19"]');
      await page.route("**/admin/v1/**", route => {
        const url = new URL(route.request().url());
        if (url.pathname === "/admin/v1/dashboard/contributors") {
          if (expired || unavailable) return route.fulfill({ status: expired ? 401 : 503, json: { error: { code: "fixture" } } });
          return route.fulfill({ json: url.searchParams.has("after") ? pageFixture([{ ...contributorFixture, contributorId: "contributor-20" }]) : pageFixture(rows, cursor) });
        }
        if (url.pathname.endsWith("/missing")) return route.fulfill({ status: 404, json: { error: { code: "resource_not_found" } } });
        return route.fulfill({ json: operatorFixture(url.pathname) });
      });
      await page.goto("/admin/#/contributors");
      await expect(page.getByRole("link", { name: /^Inspect contributor-/ })).toHaveCount(20);
      await page.getByRole("link", { name: "Next page", exact: true }).click();
      await expect(page.getByRole("link", { name: "Inspect contributor-20", exact: true })).toBeVisible();
      unavailable = true;
      await page.getByRole("button", { name: "Refresh data" }).click();
      await expect(page.getByRole("alert")).toHaveText("Stale snapshot. Refresh unavailable.");
      await expect(page.getByRole("link", { name: "Inspect contributor-20", exact: true })).toBeVisible();
      unavailable = false; expired = true;
      await page.getByRole("button", { name: "Refresh data" }).click();
      await expect(page.getByText("Sign-in required", { exact: true })).toBeVisible();
      await expect(page.getByRole("navigation", { name: "Operator pages" })).toHaveCount(0);
      const context = page.url();
      expired = false;
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await expect(page.getByRole("link", { name: "Inspect contributor-20", exact: true })).toBeVisible();
      expect(page.url()).toBe(context);
      await page.goto("/admin/#/contributors?selected=missing");
      await expect(page.getByRole("alert")).toHaveText("Selected resource not found");
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    });
    test("reasoned moderation, cancel, retained evidence, vote reversal and report review", async ({ page }) => {
      const actions: Record<string, unknown>[] = [];
      let contributorState = "active", filterQuarantined = 0, excluded = 0, reportState = "open", revision = 2;
      await page.route("**/admin/v1/**", route => {
        const request = route.request(), path = new URL(request.url()).pathname;
        if (request.method() === "POST") {
          expect(request.headers()["x-community-operator"]).toBe("1");
          const body = request.postDataJSON(); actions.push(body); revision += 1;
          if (body.command === "suspend") contributorState = "suspended";
          if (body.command === "unsuspend") contributorState = "active";
          if (body.command === "quarantine") filterQuarantined = 1;
          if (body.command === "restore") filterQuarantined = 0;
          if (body.command === "exclude_vote") excluded = 1;
          if (body.command === "restore_vote") excluded = 0;
          if (body.command === "resolve_report") reportState = "resolved";
          return route.fulfill({ json: { version: 1, actionId: body.actionId,
            resource: `${body.resource}:${body.targetId}${body.contributorId ? `:${body.contributorId}` : ""}`, revision: body.expectedRevision + 1, outcome: "applied" } });
        }
        if (path.startsWith("/admin/v1/moderation/")) {
          const [, , , , kind, id, contributorId] = path.split("/");
          const state = kind === "vote" ? { filterId: id, contributorId, vote: "down", excluded, updatedAt: 1 } :
            kind === "contributor" ? { contributorId: id, displayName: contributorFixture.displayName, state: contributorState, quarantined: 0 } :
            kind === "report" ? { ...reportFixture, state: reportState } :
            { filterId: id, versionId: "version-1", state: "active", mergedInto: null, quarantined: filterQuarantined, served: 1, confidence: 0.5, contributorCount: 2 };
          const audit = actions.filter(action => action.targetId === id).slice(-20).map(action => ({ actionId: action.actionId,
            actor: "fixture-operator", command: action.command, reason: action.reason, outcome: "applied", expectedRevision: action.expectedRevision, createdAt: Date.now() }));
          return route.fulfill({ json: { version: 1, resource: `${kind}:${id}${contributorId ? `:${contributorId}` : ""}`, revision, state, audit } });
        }
        return route.fulfill({ json: operatorFixture(path) });
      });
      await page.goto("/admin/#/contributors?selected=contributor-1");
      const suspend = page.getByRole("button", { name: "Suspend contributor", exact: true });
      await suspend.click(); const dialog = page.getByRole("dialog");
      await expect(dialog).toBeVisible(); await expect(dialog.getByText(/all their installations/)).toBeVisible();
      await expect(dialog.getByRole("button", { name: "Confirm action" })).toBeDisabled();
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await expect(suspend).toBeFocused(); expect(actions).toHaveLength(0);
      for (const label of ["Suspend contributor", "Restore contributor access"]) {
        await page.getByRole("button", { name: label, exact: true }).click();
        await dialog.getByLabel("Operator reason").fill("Reviewed synthetic report <script>not executed</script>");
        await expect(dialog.getByRole("heading", { name: label, exact: true })).toBeVisible();
        await expect(dialog.getByRole("button", { name: "Confirm action" })).toBeVisible();
        expect(await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze().then(result => result.violations)).toEqual([]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
        await page.screenshot({ path: `test-results/css58-confirm-${width}.png` });
        await dialog.getByRole("button", { name: "Confirm action" }).click(); await expect(dialog).toHaveCount(0);
        await expect(page.getByText("Service snapshot loaded")).toBeVisible();
      }
      await page.getByRole("link", { name: "Look up a vote by this contributor", exact: true }).click();
      await page.getByLabel("Filter ID", { exact: true }).fill("filter-1");
      await page.getByRole("button", { name: "Inspect resource", exact: true }).click();
      for (const label of ["Exclude vote", "Restore vote"]) {
        await page.getByRole("button", { name: label, exact: true }).click();
        await dialog.getByLabel("Operator reason").fill("Reviewed individual abusive vote");
        await dialog.getByRole("button", { name: "Confirm action" }).click(); await expect(dialog).toHaveCount(0);
      }
      await page.goto("/admin/#/inspect?kind=filter&selected=filter-1");
      for (const label of ["Quarantine filter", "Restore filter"]) {
        await page.getByRole("button", { name: label, exact: true }).click();
        await dialog.getByLabel("Operator reason").fill("Reviewed filter evidence");
        await dialog.getByRole("button", { name: "Confirm action" }).click(); await expect(dialog).toHaveCount(0);
        await expect(page.getByText("Service snapshot loaded")).toBeVisible();
      }
      await page.goto("/admin/#/reports?selected=report-1");
      await expect(page.getByRole("link", { name: "Inspect reported filter" })).toHaveAttribute("href", "#/inspect?selected=filter-1&kind=filter");
      await page.getByRole("button", { name: "Resolve report", exact: true }).click();
      await expect(dialog.getByText(/does not quarantine/)).toBeVisible();
      await dialog.getByLabel("Operator reason").fill("Report reviewed; no further action required");
      await dialog.getByRole("button", { name: "Confirm action" }).click(); await expect(dialog).toHaveCount(0);
      await expect(page.getByText("resolved", { exact: true })).toBeVisible();
      expect(actions.map(action => action.command)).toEqual(["suspend", "unsuspend", "exclude_vote", "restore_vote", "quarantine", "restore", "resolve_report"]);
      expect(new Set(actions.map(action => action.actionId)).size).toBe(7);
      expect(await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze().then(result => result.violations)).toEqual([]);
    });
    test("conflicts require review, uncertain retry stays exact and session expiry clears confirmations", async ({ page }) => {
      let mode = "conflict";
      const writes: string[] = [];
      await page.route("**/admin/v1/**", route => {
        const request = route.request(), path = new URL(request.url()).pathname;
        if (request.method() === "POST") {
          writes.push(request.postData()!);
          if (mode === "offline") return route.abort("failed");
          return route.fulfill({ status: mode === "conflict" ? 409 : 401,
            json: { error: { code: mode === "conflict" ? "moderation_conflict" : "operator_denied" } } });
        }
        return route.fulfill({ json: operatorFixture(path) });
      });
      await page.goto("/admin/#/reports?selected=report-1");
      const open = async () => {
        await page.getByRole("button", { name: "Resolve report", exact: true }).click();
        await page.getByRole("dialog").getByLabel("Operator reason").fill("Reviewed report");
        await page.getByRole("button", { name: "Confirm action", exact: true }).click();
      };
      await open(); await expect(page.getByText(/State conflict/)).toBeVisible();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      mode = "offline"; await open(); await expect(page.getByRole("alert")).toContainText("Outcome unconfirmed");
      await page.getByRole("button", { name: "Close without confirming outcome" }).click();
      await expect(page.getByRole("button", { name: "Resolve report", exact: true })).toBeDisabled();
      await page.getByRole("button", { name: "Review saved action" }).click();
      await page.getByRole("button", { name: "Retry saved action" }).click();
      await expect(page.getByRole("alert")).toContainText("Outcome unconfirmed");
      expect(writes[1]).toBe(writes[2]); mode = "expired";
      await page.getByRole("button", { name: "Retry saved action" }).click();
      await expect(page.getByText("Sign-in required", { exact: true })).toBeVisible();
      await expect(page.getByRole("dialog")).toHaveCount(0); expect(writes[2]).toBe(writes[3]);
      expect(page.url()).toContain("#/reports?selected=report-1");
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    });
  });
}