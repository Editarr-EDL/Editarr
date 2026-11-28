import { expect, test } from "@playwright/test";
import { contributorFixture, reportFixture } from "../src/settings/community-operator/test-fixtures";
import { healthFixture } from "../src/settings/community-operator/test-fixtures-pages";
import { expectAccessible, expectNoOverflow, mockOperatorApi, strictOperatorPage, watchCsp } from "./operator-mock";

for (const width of [320, 1440]) {
  test.describe(`operator access ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });
    test("verified identity, re-verification, image, strict CSP and access isolation", async ({ page }) => {
      const localRequests: string[] = [];
      page.on("request", request => { if (new URL(request.url()).pathname.startsWith("/api/")) localRequests.push(request.url()); });
      let reads = 0;
      await strictOperatorPage(page);
      await mockOperatorApi(page, path => path === "/admin/v1/session" ?
        { version: 1, subject: `${++reads === 1 ? "first" : "fresh"}-${"operator-subject-".repeat(12)}` } : undefined);
      const csp = await watchCsp(page);
      await page.goto("/admin/");
      await expect(page.getByRole("status").filter({ hasText: "Access verified" })).toHaveCount(1);
      await expect(page.getByRole("heading", { level: 1 })).toContainText("Good");
      const logo = page.locator(".sidebar .brand img");
      expect(await logo.evaluate(image => (image as HTMLImageElement).naturalWidth)).toBeGreaterThan(30);
      if (width < 900) await page.getByRole("button", { name: "Open navigation" }).click();
      await expect(page.locator(".side-foot .subject")).toContainText("first-");
      await page.getByRole("button", { name: "Refresh access" }).click();
      if (width < 900) await page.getByRole("button", { name: "Open navigation" }).click();
      await expect(page.locator(".side-foot .subject")).toContainText("fresh-");
      expect(localRequests).toEqual([]);
      expect(csp.violations).toEqual([]);
      expect(await csp.inlineStyles()).toBe(0);
      await expectNoOverflow(page, width);
    });
    for (const [status, message] of [[401, "Sign-in required"], [403, "Operator access denied"], [503, "Access verification unavailable"]] as const) {
      test(`denial ${status} has no verified identity or local sign-in`, async ({ page }) => {
        await page.route("**/admin/v1/session", route => route.fulfill({ status, json: { error: { code: "fixture-denial" } } }));
        await page.goto("/admin/");
        await expect(page.getByText(message, { exact: true })).toBeVisible();
        await expect(page.getByRole("navigation", { name: "Operator pages" })).toHaveCount(0);
        await expect(page.locator("input[type=password]")).toHaveCount(0);
        await expectNoOverflow(page, width);
        await expectAccessible(page);
      });
    }
    test("offline and login HTML fail closed", async ({ page }) => {
      await page.route("**/admin/v1/session", route => route.abort("failed"));
      await page.goto("/admin/");
      await expect(page.getByRole("alert")).toHaveText("Access verification unavailable");
      await page.unroute("**/admin/v1/session");
      await page.route("**/admin/v1/session", route => route.fulfill({ contentType: "text/html", body: "<html>Login</html>" }));
      await page.getByRole("button", { name: "Refresh access" }).click();
      await expect(page.getByRole("alert")).toHaveText("Access verification unavailable");
      await expect(page.getByRole("navigation", { name: "Operator pages" })).toHaveCount(0);
    });
    test("stale snapshot, expired-session recovery and missing resource", async ({ page }) => {
      let mode: "ok" | "down" | "expired" = "ok";
      await mockOperatorApi(page, path => path === "/admin/v1/health" && mode !== "ok" ?
        { status: mode === "down" ? 503 : 401, json: { error: { code: "fixture" } } } : path.endsWith("/missing") ?
          { status: 404, json: { error: { code: "resource_not_found" } } } : undefined);
      await page.goto("/admin/#/health");
      await expect(page.getByRole("heading", { name: /Ready · all checks passing/ })).toBeVisible();
      mode = "down";
      await page.getByRole("button", { name: "Re-check" }).click();
      await expect(page.getByRole("alert").filter({ hasText: "Stale snapshot" })).toBeVisible();
      await expect(page.getByRole("heading", { name: /Ready · all checks passing/ })).toBeVisible();
      mode = "expired";
      await page.getByRole("button", { name: "Re-check" }).click();
      await expect(page.getByText("Sign-in required", { exact: true })).toBeVisible();
      const context = page.url();
      mode = "ok";
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await expect(page.getByRole("heading", { name: /Ready · all checks passing/ })).toBeVisible();
      expect(page.url()).toBe(context);
      await page.goto("/admin/#/inspect?kind=report&selected=missing");
      await expect(page.getByRole("alert")).toHaveText("Selected resource not found");
      await expectNoOverflow(page, width);
      expect(healthFixture.ready).toBe(true);
    });
    test("reasoned moderation in the inspector: suspension scope, vote reversal, filter quarantine and report review", async ({ page }) => {
      const actions: Record<string, unknown>[] = [];
      let contributorState = "active", filterQuarantined = 0, excluded = 0, reportState = "open", revision = 2;
      await mockOperatorApi(page, (path, method, body) => {
        if (method === "POST" && path === "/admin/v1/moderation") {
          const intent = body as Record<string, string | number>; actions.push(intent); revision += 1;
          if (intent.command === "suspend") contributorState = "suspended";
          if (intent.command === "unsuspend") contributorState = "active";
          if (intent.command === "quarantine") filterQuarantined = 1;
          if (intent.command === "restore") filterQuarantined = 0;
          if (intent.command === "exclude_vote") excluded = 1;
          if (intent.command === "restore_vote") excluded = 0;
          if (intent.command === "resolve_report") reportState = "resolved";
          return { version: 1, actionId: intent.actionId, resource: `${intent.resource}:${intent.targetId}${intent.contributorId ? `:${intent.contributorId}` : ""}`,
            revision: Number(intent.expectedRevision) + 1, outcome: "applied" };
        }
        if (!path.startsWith("/admin/v1/moderation/")) return undefined;
        const [, , , , kind, id, contributorId] = path.split("/");
        const state = kind === "vote" ? { filterId: id, contributorId, vote: "down", excluded, updatedAt: 1 } :
          kind === "contributor" ? { contributorId: id, displayName: contributorFixture.displayName, state: contributorState, quarantined: 0 } :
            kind === "report" ? { ...reportFixture, state: reportState } :
              { filterId: id, versionId: "version-1", state: "active", mergedInto: null, quarantined: filterQuarantined, served: 1, confidence: 0.5, contributorCount: 2 };
        return { version: 1, resource: `${kind}:${id}${contributorId ? `:${contributorId}` : ""}`, revision, state, audit: [] };
      });
      await page.goto("/admin/#/inspect?kind=contributor&selected=contributor-1");
      const suspend = page.getByRole("button", { name: "Suspend contributor", exact: true });
      await suspend.click(); const dialog = page.getByRole("dialog");
      await expect(dialog.getByText(/all their installations/)).toBeVisible();
      await expect(dialog.getByRole("region", { name: "Affected credentials" })).toBeVisible();
      await expect(dialog.getByRole("button", { name: "Confirm action" })).toBeDisabled();
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      await expect(suspend).toBeFocused(); expect(actions).toHaveLength(0);
      for (const label of ["Suspend contributor", "Restore contributor access"]) {
        await page.getByRole("button", { name: label, exact: true }).click();
        await dialog.getByLabel("Operator reason").fill("Reviewed synthetic report <script>not executed</script>");
        await expectAccessible(page); await expectNoOverflow(page, width);
        await dialog.getByRole("button", { name: "Confirm action" }).click(); await expect(dialog).toHaveCount(0);
      }
      await page.goto("/admin/#/inspect?kind=vote&selected=filter-1&contributor=contributor-1");
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
      }
      await page.goto("/admin/#/inspect?kind=report&selected=report-1");
      await expect(page.getByRole("region", { name: "Resource inspection" }).getByText("<script>window.hostileTextExecuted=true</script>", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "Resolve report", exact: true }).click();
      await expect(dialog.getByText(/does not quarantine/)).toBeVisible();
      await dialog.getByLabel("Operator reason").fill("Report reviewed; no further action required");
      await dialog.getByRole("button", { name: "Confirm action" }).click(); await expect(dialog).toHaveCount(0);
      await expect(page.getByText("resolved", { exact: true })).toBeVisible();
      expect(actions.map(action => action.command)).toEqual(["suspend", "unsuspend", "exclude_vote", "restore_vote", "quarantine", "restore", "resolve_report"]);
      expect(new Set(actions.map(action => action.actionId)).size).toBe(7);
      expect(await page.evaluate(() => (window as Window & { hostileTextExecuted?: boolean }).hostileTextExecuted)).toBeUndefined();
      await expectAccessible(page);
    });
    test("conflicts require review, uncertain retry stays exact and session expiry clears confirmations", async ({ page }) => {
      let mode = "conflict";
      const writes: string[] = [];
      await mockOperatorApi(page);
      await page.route("**/admin/v1/moderation", route => {
        writes.push(route.request().postData()!);
        if (mode === "offline") return route.abort("failed");
        return route.fulfill({ status: mode === "conflict" ? 409 : 401, json: { error: { code: mode === "conflict" ? "moderation_conflict" : "operator_denied" } } });
      });
      await page.goto("/admin/#/inspect?kind=report&selected=report-1");
      const open = async () => {
        await page.getByRole("button", { name: "Resolve report", exact: true }).click();
        await page.getByRole("dialog").getByLabel("Operator reason").fill("Reviewed report");
        await page.getByRole("button", { name: "Confirm action", exact: true }).click();
      };
      await open(); await expect(page.getByText(/State conflict/)).toBeVisible();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      mode = "offline"; await open(); await expect(page.getByRole("dialog").getByRole("alert")).toContainText("Outcome unconfirmed");
      await page.getByRole("button", { name: "Close without confirming outcome" }).click();
      await expect(page.getByRole("button", { name: "Resolve report", exact: true })).toBeDisabled();
      await page.getByRole("button", { name: "Review saved action" }).click();
      await page.getByRole("button", { name: "Retry saved action" }).click();
      await expect(page.getByRole("dialog").getByRole("alert")).toContainText("Outcome unconfirmed");
      expect(writes[1]).toBe(writes[2]); mode = "expired";
      await page.getByRole("button", { name: "Retry saved action" }).click();
      await expect(page.getByText("Sign-in required", { exact: true })).toBeVisible();
      await expect(page.getByRole("dialog")).toHaveCount(0); expect(writes[2]).toBe(writes[3]);
      expect(page.url()).toContain("#/inspect?kind=report&selected=report-1");
      await expectNoOverflow(page, width);
    });
  });
}
