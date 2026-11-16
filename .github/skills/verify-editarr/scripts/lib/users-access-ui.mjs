import assert from "node:assert/strict";
import { modeRadio, myFiltersHeading, newAccountModeHeading, openFilters } from "./filters-page.mjs";

const SCOPED_LIBRARY = "b".repeat(32);

/** Add the synthetic library to a scope editor unless it is already listed. */
async function addLibrary(editor) {
  const listed = editor.getByRole("button", { name: `Remove library ${SCOPED_LIBRARY}`, exact: true });
  if (!await listed.count()) {
    await editor.getByLabel("Jellyfin library ID", { exact: true }).fill(SCOPED_LIBRARY);
    await editor.getByRole("button", { name: "Add library", exact: true }).click();
  }
  await listed.waitFor();
}

export async function verifyUsersAccess(ui) {
  const { run, admin, secondAdmin, owner, adminPassword, jf, api, open, signIn, directory, details, expect, save, checkAxe, record } = ui;
  const ownerProof = { reauthentication: { password: owner.password } };
  const accounts = (await api(admin.context, "GET", "/api/users")).items;
  const other = accounts.find(account => account.display_name === "UI Other");
  const member = accounts.find(account => account.display_name === "UI Member");
  const administrator = accounts.find(account => account.display_name === "UI Admin");
  const linked = accounts.find(account => account.source === "jellyfin" && account.display_name === "jf-linked");
  assert.ok(other && member && administrator && linked);
  async function proof(page, title, action, password = owner.password) {
    const modal = page.getByRole("dialog", { name: title, exact: true });
    await expect(modal.getByLabel(/^(?:Current|Jellyfin) password$/)).toBeFocused();
    await modal.getByLabel(/^(?:Current|Jellyfin) password$/).fill(password);
    await modal.getByRole("button", { name: action, exact: true }).click();
    await expect(modal).toHaveCount(0);
  }
  async function expand(page, name) {
    const summary = page.locator("summary").filter({ hasText: new RegExp(`^${name}$`) });
    if (!await summary.evaluate(element => element.parentElement.open)) await summary.click();
  }
  async function close(page) {
    await page.getByRole("dialog", { name: "Account details", exact: true }).getByRole("button", { name: "Close", exact: true }).click();
  }
  async function bounds(page, name) {
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, name);
    await save(page, name); await checkAxe(page, name);
  }
  async function setDefaultMode(actor, mode) {
    await openFilters(actor.page, run.manifest.origin, "server");
    await expect(actor.page.getByRole("heading", { name: "Filters", exact: true })).toBeVisible();
    const radio = modeRadio(actor.page, "server", mode);
    await expect(radio).toBeEnabled();
    if (!await radio.isChecked()) {
      await radio.check();
      await expect(actor.page.getByText(mode === "copy" ? "New accounts will get their own copy." : "New accounts will keep in sync.", { exact: true })).toBeVisible();
    }
    assert.equal((await api(actor.context, "GET", "/api/settings/filters/default-mode")).mode, mode);
  }
  const initial = await api(admin.context, "GET", "/api/settings/filters");
  const ruleId = "00000000-0000-4000-8000-000000003030";
  const rule = { id: ruleId, disposition: "deny", evidence_source: null, category: "nudity", subcategory: "nudity", label_contains: null, severity: null, mcf_channel: "video" };
  await api(admin.context, "PUT", "/api/settings/filters", { revision: initial.revision, selection_rules: [...initial.selection_rules, rule], severity_defaults: initial.severity_defaults, ...ownerProof });
  for (const [method, path, body] of [
    ["GET", "/api/roles/defaults"],
    ["PATCH", `/api/users/${other.id}/permissions`, { overrides: { "scans.start": "allow" } }],
    ["PUT", `/api/users/${other.id}/role`, { role: "admin" }],
    ["PUT", `/api/settings/filters/users/${other.id}/policy`, { policy_revision: 1, rules: [], severities: [] }],
  ]) {
    const response = await api(secondAdmin.context, method, path, body, 403);
    assert.equal(response.code, "capability_denied");
  }
  await api(admin.context, "POST", `/api/users/${linked.id}/role-sync`, ownerProof, 404);
  record("MU30 direct backend refusals protect owner-only roles, overrides, and enforcement; Jellyfin role sync is gone");

  for (const width of [1440, 320]) {
    await admin.page.setViewportSize({ width, height: 900 }); await directory(admin.page);
    await expect(admin.page.locator("summary").filter({ hasText: /^Default filter mode$/ })).toHaveCount(0);
    for (const [capability, value] of [["scans.start", "allow"], ["filters.preferences.edit.other", "deny"]]) {
      await expand(admin.page, "Role permission defaults");
      await admin.page.getByRole("combobox", { name: "Default role", exact: true }).selectOption("user");
      await admin.page.getByRole("combobox", { name: "Default permission", exact: true }).selectOption(capability);
      await admin.page.getByRole("combobox", { name: "Role permission value", exact: true }).selectOption(value);
      await admin.page.getByRole("button", { name: "Save role default", exact: true }).click();
      await proof(admin.page, "Save role permission default", "Confirm role default");
      await admin.page.reload(); await directory(admin.page);
      await expand(admin.page, "Role permission defaults");
      await admin.page.getByRole("combobox", { name: "Default permission", exact: true }).selectOption(capability);
      await expect(admin.page.getByRole("combobox", { name: "Role permission value", exact: true })).toHaveValue(value);
      await admin.page.getByRole("button", { name: "Save role default", exact: true }).click();
      await proof(admin.page, "Save role permission default", "Confirm role default");
      const stored = (await api(admin.context, "GET", "/api/roles/defaults")).user.find(row => row.capability === capability);
      assert.deepEqual(stored, { capability, granted: value === "allow", catalog_default: value === "allow", explicit: true });
    }
    record(`MU30 ${width}px explicit catalog-equal allow and deny survive reload and untouched save`);
    await expand(admin.page, "Role permission defaults");
    await admin.page.getByRole("combobox", { name: "Default role", exact: true }).selectOption("user");
    await admin.page.getByRole("combobox", { name: "Default permission", exact: true }).selectOption("scans.start");
    await admin.page.getByRole("combobox", { name: "Role permission value", exact: true }).selectOption("deny");
    await admin.page.getByRole("button", { name: "Save role default", exact: true }).click();
    await proof(admin.page, "Save role permission default", "Confirm role default");
    assert.equal((await api(admin.context, "GET", `/api/users/${other.id}/permissions`)).effective.includes("scans.start"), false);
    await bounds(admin.page, `mu30-role-default-${width}`);
    let dialog = await details(admin.page, "UI Other"); await expand(dialog, "Role and permissions");
    await dialog.getByRole("combobox", { name: "Permission", exact: true }).selectOption("scans.start");
    for (const effect of ["allow", "inherit", "deny"]) {
      await dialog.getByRole("combobox", { name: "Permission override", exact: true }).selectOption(effect);
      await dialog.getByRole("button", { name: "Save permission override", exact: true }).click();
      await proof(admin.page, "Save permission override", "Confirm change");
      const permissions = await api(admin.context, "GET", `/api/users/${other.id}/permissions`);
      assert.equal(permissions.overrides["scans.start"], effect === "inherit" ? undefined : effect);
      assert.equal(permissions.effective.includes("scans.start"), effect === "allow");
      await expect(dialog.getByText(`Stored override: ${effect}.`, { exact: false })).toBeVisible();
    }
    await bounds(admin.page, `mu30-permission-override-${width}`); await close(admin.page);
    await admin.page.reload(); await directory(admin.page); dialog = await details(admin.page, "UI Other");
    await expand(dialog, "Role and permissions"); await dialog.getByRole("combobox", { name: "Permission", exact: true }).selectOption("scans.start");
    await expect(dialog.getByRole("combobox", { name: "Permission override", exact: true })).toHaveValue("deny"); await close(admin.page);
    record(`MU30 ${width}px role defaults and allow/inherit/deny overrides persist with backend effective permissions`);

    await expand(admin.page, "Local media default");
    const defaultMedia = admin.page.locator("details.account-policy-section").filter({ has: admin.page.locator("summary").filter({ hasText: /^Local media default$/ }) });
    await defaultMedia.getByRole("combobox", { name: "Media scope", exact: true }).selectOption("selected");
    const defaultNow = await api(admin.context, "GET", "/api/media-access/default");
    await expect(defaultMedia.getByRole("button", { name: `Remove library ${SCOPED_LIBRARY}`, exact: true })).toHaveCount(defaultNow.sources.some(source => source.id === SCOPED_LIBRARY) ? 1 : 0);
    await addLibrary(defaultMedia);
    await defaultMedia.getByRole("button", { name: "Save media scope", exact: true }).click(); await proof(admin.page, "Save local media default", "Confirm media default");
    const savedDefault = await api(admin.context, "GET", "/api/media-access/default"); assert.equal(savedDefault.all_sources, false); assert.ok(savedDefault.sources.some(source => source.kind === "jellyfin_library" && source.id === SCOPED_LIBRARY));
    dialog = await details(admin.page, "UI Other");
    assert.equal(await dialog.getByText("Local media access", { exact: true }).count(), 0, "Jellyfin decides a linked account's media");
    assert.deepEqual(await api(admin.context, "GET", `/api/users/${other.id}/media-access`), { linked: true, scope: null });
    await bounds(admin.page, `mu30-media-scope-${width}`); await close(admin.page);
    record(`MU30 ${width}px the local media default matches a fresh GET and a linked account has no local media scope`);

    await setDefaultMode(admin, "live"); await setDefaultMode(admin, "copy");
    await bounds(admin.page, `mu30-shared-default-mode-owner-${width}`); await directory(admin.page);
    dialog = await details(admin.page, "UI Other"); await expand(dialog, "Filter mode and policy");
    await dialog.getByRole("combobox", { name: "Assigned filter mode", exact: true }).selectOption("live"); await dialog.getByRole("button", { name: "Save filter mode", exact: true }).click(); await expect(dialog.getByText("Filter mode saved.", { exact: true })).toBeVisible();
    assert.equal((await api(admin.context, "GET", `/api/settings/filters/users/${other.id}`)).mode, "live");
    const ruleCheck = dialog.getByRole("checkbox", { name: new RegExp(`^${ruleId}:`) });
    const policy = await api(admin.context, "GET", `/api/settings/filters/users/${other.id}`);
    await expect(ruleCheck).toBeChecked({ checked: policy.enforced_rules.includes(ruleId) });
    if (!await ruleCheck.isChecked()) await ruleCheck.click();
    await expand(dialog, "Enforced instance severities");
    const severity = dialog.getByRole("checkbox", { name: /^nudity: / });
    if (!await severity.isChecked()) await severity.click();
    await dialog.getByRole("button", { name: "Save enforced policy", exact: true }).click(); await proof(admin.page, "Save enforced filter policy", "Confirm enforced policy");
    const enforced = await api(admin.context, "GET", `/api/settings/filters/users/${other.id}`);
    assert.ok(enforced.enforced_rules.includes(ruleId)); assert.ok(enforced.enforced_severities.includes("nudity")); assert.equal(enforced.mode_locked, true);
    await dialog.getByRole("combobox", { name: "Assigned filter mode", exact: true }).selectOption("copy"); await dialog.getByRole("button", { name: "Save filter mode", exact: true }).click();
    await expect(dialog.getByText(/Filter mode: copy\./)).toBeVisible();
    assert.ok((await api(admin.context, "GET", `/api/settings/filters/users/${other.id}`)).enforced_rules.includes(ruleId));
    await bounds(admin.page, `mu30-enforcement-${width}`); await close(admin.page);
    record(`MU30 ${width}px default and assigned filter modes plus enforced rule/severity persist across modes`);

    dialog = await details(admin.page, "jf-linked"); await expand(dialog, "Role and permissions");
    await dialog.getByRole("combobox", { name: "Assigned role", exact: true }).selectOption("admin"); await dialog.getByRole("button", { name: "Assign role", exact: true }).click(); await proof(admin.page, "Assign account role", "Confirm change");
    assert.equal((await api(admin.context, "GET", `/api/users/${linked.id}`)).role, "admin");
    await expect(dialog.getByRole("button", { name: "Restore Jellyfin role sync", exact: true })).toHaveCount(0);
    await expect(dialog.getByText(/Role origin/)).toHaveCount(0);
    await dialog.getByRole("combobox", { name: "Assigned role", exact: true }).selectOption("user"); await dialog.getByRole("button", { name: "Assign role", exact: true }).click(); await proof(admin.page, "Assign account role", "Confirm change");
    assert.equal((await api(admin.context, "GET", `/api/users/${linked.id}`)).role, "user");
    await expect(dialog.getByText("Unmatched-media exceptions", { exact: true })).toHaveCount(0);
    await bounds(admin.page, `mu30-linked-role-${width}`); await close(admin.page);
    record(`MU30 ${width}px owner sets a linked account's role both ways with no Jellyfin role sync and no unmatched-media exceptions`);
  }

  for (const width of [1440, 320]) {
    await secondAdmin.page.setViewportSize({ width, height: 900 });
    await setDefaultMode(secondAdmin, "live"); await setDefaultMode(secondAdmin, "copy");
    await bounds(secondAdmin.page, `mu30-shared-default-mode-admin-${width}`);
  }
  record("MU30 admin edits the shared Filters default-mode control at 1440/320");
  await directory(secondAdmin.page);
  await secondAdmin.page.getByRole("checkbox", { name: "Select UI Other", exact: true }).check();
  await secondAdmin.page.getByRole("checkbox", { name: "Select UI Member", exact: true }).check();
  await api(admin.context, "PUT", `/api/users/${other.id}/role`, { role: "admin", ...ownerProof });
  await secondAdmin.page.getByRole("button", { name: "Set selected filter modes", exact: true }).click();
  await secondAdmin.page.getByRole("combobox", { name: "Selected accounts filter mode", exact: true }).selectOption("live");
  await secondAdmin.page.getByRole("button", { name: "Confirm selected changes", exact: true }).click();
  const mixed = secondAdmin.page.getByRole("region", { name: "Bulk access results" });
  await expect(mixed).toContainText("UI Other: refused"); await expect(mixed).toContainText("UI Member: applied");
  assert.equal((await api(admin.context, "GET", `/api/settings/filters/users/${other.id}`)).mode, "copy");
  assert.equal((await api(admin.context, "GET", `/api/settings/filters/users/${member.id}`)).mode, "live");
  for (const width of [320, 1440]) {
    await secondAdmin.page.setViewportSize({ width, height: 900 });
    await Promise.all([
      secondAdmin.page.waitForResponse(response => new URL(response.url()).pathname === "/api/auth/status"),
      secondAdmin.page.evaluate(() => dispatchEvent(new Event("focus"))),
    ]);
    await expect(mixed).toContainText("UI Other: refused"); await expect(mixed).toContainText("UI Member: applied");
    await bounds(secondAdmin.page, `mu30-bulk-protected-${width}`);
  }
  await api(admin.context, "PUT", `/api/users/${other.id}/role`, { role: "user", ...ownerProof });
  record("MU30 mixed bulk reports protected refusal and eligible application across auth refreshes with axe and overflow at 320/1440");

  await directory(secondAdmin.page);
  await expect(secondAdmin.page.getByText("Local media default", { exact: true })).toBeVisible();
  let dialog = await details(secondAdmin.page, "UI Other");
  await expect(dialog.getByText("Filter mode and policy", { exact: true })).toBeVisible();
  await close(secondAdmin.page);
  await api(admin.context, "PATCH", `/api/users/${administrator.id}/permissions`, { overrides: { "media_access.user.edit": "deny", "media_access.defaults.edit": "deny", "filters.mode.assign.other": "deny", "filters.instance_mode.edit": "deny" }, ...ownerProof });
  await secondAdmin.page.evaluate(() => dispatchEvent(new Event("focus")));
  await expect(secondAdmin.page.getByText("Local media default", { exact: true })).toHaveCount(0);
  for (const width of [1440, 320]) {
    await secondAdmin.page.setViewportSize({ width, height: 900 }); await directory(secondAdmin.page);
    dialog = await details(secondAdmin.page, "UI Other");
    for (const name of ["Role and permissions", "Local media access", "Filter mode and policy", "Unmatched-media exceptions", "Ownership transfer"]) assert.equal(await dialog.getByText(name, { exact: true }).count(), 0);
    await bounds(secondAdmin.page, `mu30-admin-capability-removal-${width}`); await close(secondAdmin.page);
    await secondAdmin.page.getByRole("checkbox", { name: "Select UI Other", exact: true }).check();
    for (const name of ["Set selected media access", "Set selected filter modes", "Assign selected roles"]) await expect(secondAdmin.page.getByRole("button", { name, exact: true })).toHaveCount(0);
    await openFilters(secondAdmin.page, run.manifest.origin);
    await expect(myFiltersHeading(secondAdmin.page)).toBeVisible();
    await expect(newAccountModeHeading(secondAdmin.page)).toHaveCount(0);
    await bounds(secondAdmin.page, `mu30-shared-default-mode-denied-${width}`);
  }
  await api(secondAdmin.context, "PUT", `/api/users/${other.id}/media-access`, { inherit: true, reauthentication: { password: adminPassword } }, 403);
  await api(secondAdmin.context, "PUT", `/api/settings/filters/users/${other.id}/mode`, { revision: 1, mode: "live" }, 403);
  await api(secondAdmin.context, "PUT", "/api/settings/filters/default-mode", { mode: "live" }, 403);
  await api(admin.context, "PATCH", `/api/users/${administrator.id}/permissions`, { overrides: { "media_access.user.edit": "inherit", "media_access.defaults.edit": "inherit", "filters.mode.assign.other": "inherit", "filters.instance_mode.edit": "inherit" }, ...ownerProof });
  record("MU30 live principal revision removes admin media and mode controls and direct backend writes are refused");
  await verifyExceptionsRemoved({ run, api, admin, linked, ownerProof });
  record("a linked account's media access has no exceptions and the owner's exception write is an unknown route");

  for (const account of [other, linked]) {
    const detail = await api(admin.context, "GET", `/api/users/${account.id}`);
    assert.deepEqual([detail.source, "local_credential" in detail, "password_change_required" in detail], ["jellyfin", false, false]);
  }
  await api(admin.context, "PUT", `/api/users/${other.id}/status`, { status: "disabled" });
  assert.equal((await api(admin.context, "POST", `/api/users/${other.id}/ownership`, ownerProof, 409)).code, "successor_disabled");
  assert.equal((await api(admin.context, "POST", `/api/users/${linked.id}/ownership`, {}, 403)).code, "reauthentication_required");
  await directory(admin.page); dialog = await details(admin.page, "UI Other");
  await expect(dialog.getByRole("button", { name: "Transfer ownership", exact: true })).toBeDisabled(); await close(admin.page);
  await api(admin.context, "PUT", `/api/users/${other.id}/status`, { status: "active" });
  dialog = await details(admin.page, "jf-linked"); await dialog.getByRole("button", { name: "Transfer ownership", exact: true }).click();
  const transferring = admin.page.getByRole("dialog", { name: "Transfer ownership?", exact: true });
  await expect(transferring.getByLabel("Jellyfin password", { exact: true })).toBeFocused();
  await transferring.getByLabel("Jellyfin password", { exact: true }).fill(ui.secret());
  await transferring.getByRole("button", { name: "Transfer ownership", exact: true }).click();
  await expect(transferring.getByRole("alert")).toBeVisible(); await expect(transferring.getByLabel("Jellyfin password", { exact: true })).toHaveValue("");
  const ownerIds = accounts.filter(account => account.role === "owner").map(account => account.id);
  assert.deepEqual((await api(admin.context, "GET", "/api/users")).items.filter(account => account.role === "owner").map(account => account.id), ownerIds);
  for (const width of [320, 1440]) { await admin.page.setViewportSize({ width, height: 900 }); await bounds(admin.page, `mu30-transfer-fresh-refusal-${width}`); }
  await transferring.getByLabel("Jellyfin password", { exact: true }).fill(owner.password);
  await transferring.getByRole("button", { name: "Transfer ownership", exact: true }).click();
  await expect(transferring).toHaveCount(0);
  assert.equal((await api(secondAdmin.context, "GET", `/api/users/${linked.id}`)).role, "owner");
  await save(admin.page, "mu30-transfer-to-jellyfin-member-1440");
  const successor = await open(1440); await signIn(successor, "jf-linked", jf.linkedPassword, "users");
  await directory(successor.page); dialog = await details(successor.page, owner.username);
  await dialog.getByRole("button", { name: "Transfer ownership", exact: true }).click();
  await proof(successor.page, "Transfer ownership?", "Transfer ownership", jf.linkedPassword);
  const restoredOwners = (await api(admin.context, "GET", "/api/users")).items.filter(account => account.role === "owner");
  assert.deepEqual(restoredOwners.map(account => account.id), ownerIds);
  assert.equal((await api(admin.context, "GET", `/api/users/${linked.id}`)).role, "admin");
  await save(successor.page, "mu30-transfer-back-1440");
  record("MU30 transfer refuses a disabled successor and missing or wrong Jellyfin proof, then hands ownership to a Jellyfin-linked member and back, each confirmed with its own Jellyfin password in the UI");
  run.write("ui-access-result.json", { passed: true, viewports: [1440, 320], linkedRoleOrigin: "jellyfin", mixedBulkProtected: true, ownerRestored: true, transferRoundTrip: "passed", recipient: "jf-linked" });
}

/** Unmatched-media exceptions are gone: Jellyfin alone decides a linked account's media. */
async function verifyExceptionsRemoved({ run, api, admin, linked, ownerProof }) {
  const route = `/api/users/${linked.id}/media-access`;
  const access = await api(admin.context, "GET", route);
  assert.deepEqual(access, { linked: true, scope: null });
  await api(admin.context, "PUT", `${route}/exceptions`, { kind: "jellyfin_library", granted: true, ...ownerProof }, 404);
  assert.deepEqual(await api(admin.context, "GET", route), access, "the refused write changed nothing");
  run.write("users-exceptions-removed.json", { passed: true, access, exceptionWrite: 404,
    scope: "Real authenticated source app. The synthetic Jellyfin-linked account's media access reports only that Jellyfin decides it, and the owner's write to the removed exceptions route is unknown." });
}
