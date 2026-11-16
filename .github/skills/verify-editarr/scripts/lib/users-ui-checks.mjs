import assert from "node:assert/strict";

export async function checkUsersUI({ run, admin, secondAdmin, lastMember, linked, jf, api, status, open, signIn, directory, save, expect, checkAxe, record }) {
  const outcomes = [];
  let linkedActor = linked;
  for (const width of [1440, 320]) {
    for (const [name, actor] of [["owner", admin], ["local-user", lastMember], ["linked-user", linkedActor]]) {
      await actor.page.setViewportSize({ width, height: 900 });
      await actor.page.goto(`${run.manifest.origin}/#/settings/profile`);
      const userId = (await status(actor.context)).access.principal.user_id;
      const account = await api(actor.context, "GET", `/api/users/${userId}`);
      assert.ok(account.created_at && account.last_login_at);
      for (const [label, value] of [["Created", account.created_at], ["Last login", account.last_login_at]]) {
        const fact = actor.page.locator(".profile-page .account-facts > div").filter({ has: actor.page.getByText(label, { exact: true }) });
        await expect(fact.locator("dt")).toHaveText(label);
        await expect(fact.locator("dd")).toHaveText(new Date(value).toLocaleString());
        assert.equal(await fact.locator("input, select, textarea, button").count(), 0);
      }
      const geometry = await actor.page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }));
      assert.ok(geometry.document <= geometry.width);
      await save(actor.page, `fix1-${name}-timestamps-${width}`); await checkAxe(actor.page, `fix1-${name}-timestamps-${width}`);
      outcomes.push({ name: `${name}-timestamps-${width}`, passed: true, created_at: account.created_at, last_login_at: account.last_login_at, readOnly: true, geometry });
      run.write("ui-profile-sync-checks.json", outcomes);
      record(`${name} read-only creation and last-login facts match account GET at ${width}px`);
    }
    await secondAdmin.page.setViewportSize({ width, height: 900 }); await directory(secondAdmin.page);
    const syncResponse = secondAdmin.page.waitForResponse(response => response.url().endsWith("/api/jellyfin/sync") && response.request().method() === "POST");
    await secondAdmin.page.getByRole("button", { name: "Sync Jellyfin now", exact: true }).click();
    const original = await (await syncResponse).json();
    const linkedAccount = (await api(admin.context, "GET", "/api/users?search=jf-linked")).items.find(account => account.source === "jellyfin");
    assert.ok(linkedAccount);
    assert.equal(original.results.find(result => result.user_id === linkedAccount.id).status, "active");
    const region = secondAdmin.page.getByRole("region", { name: "Sync results" });
    await expect(region).toContainText("jf-linked: verified, verified, user, active");
    await expect(secondAdmin.page.getByRole("button", { name: "Sync Jellyfin now", exact: true })).toBeEnabled();
    const before = await jf.call("GET", `/Users/${jf.linked.Id}`);
    try {
      await jf.call("POST", `/Users/${jf.linked.Id}/Policy`, { ...before.Policy, IsDisabled: true });
      const fresh = await api(admin.context, "POST", "/api/jellyfin/sync");
      const result = fresh.results.find(entry => entry.user_id === linkedAccount.id);
      assert.equal(result.status, "disabled");
      const refreshResponse = secondAdmin.page.waitForResponse(response => response.url().endsWith("/api/jellyfin/sync") && response.request().method() === "GET");
      await secondAdmin.page.getByRole("button", { name: "Refresh sync status", exact: true }).click();
      const refreshed = await (await refreshResponse).json();
      assert.equal(refreshed.last_run.finished_at, fresh.finished_at);
      await expect(secondAdmin.page.getByText(`Last completed run: ${new Date(fresh.finished_at).toLocaleString()}`, { exact: true })).toBeVisible();
      await expect(region).toContainText(`jf-linked: ${result.outcome}, ${result.link_state}, ${result.role}, ${result.status}`);
      await expect(region).not.toContainText("jf-linked: verified, verified, user, active");
      const geometry = await secondAdmin.page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }));
      assert.ok(geometry.document <= geometry.width);
      await save(secondAdmin.page, `fix1-sync-refreshed-${width}`); await checkAxe(secondAdmin.page, `fix1-sync-refreshed-${width}`);
      outcomes.push({ name: `sync-refreshed-${width}`, passed: true, backendResult: result, lastRunFinished: refreshed.last_run.finished_at, renderedResults: await region.innerText(), geometry });
      run.write("ui-profile-sync-checks.json", outcomes);
      record(`admin refresh replaces local sync results after owner sync disables linked account at ${width}px`);
    } finally {
      await jf.call("POST", `/Users/${jf.linked.Id}/Policy`, before.Policy); await api(admin.context, "POST", "/api/jellyfin/sync");
    }
    if (width === 1440) {
      linkedActor = await open(320); await signIn(linkedActor, "jf-linked", jf.linkedPassword);
      await expect(linkedActor.page.getByRole("heading", { name: "My profile", exact: true })).toBeVisible();
    }
  }
  assert.equal(outcomes.length, 8);
}