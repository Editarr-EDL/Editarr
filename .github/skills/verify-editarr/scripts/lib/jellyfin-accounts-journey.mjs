import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { capture } from "./capture.mjs";
import { launchBrowser } from "./instance.mjs";
import { childEnvironment, pythonPath } from "./prerequisites.mjs";
import { usersUI } from "./users-ui-support.mjs";

const PARENT_REVISION = "0027_jellyfin_owner";
const IMPORT_ONLY = [
  "import sqlite3, sys",
  "from pathlib import Path",
  "from tests.activity_migration_support import _upgrade",
  `_upgrade(Path(sys.argv[1]), '${PARENT_REVISION}')`,
  "with sqlite3.connect(sys.argv[1]) as db:",
  "    db.execute(\"INSERT INTO account_settings (id, registration_mode, updated_at, jellyfin_admission) VALUES (1, 'managed', '2026-01-01', 'import_only')\")",
].join("\n");

const CLIENT = 'MediaBrowser Client="editarr-verify", Device="verify", DeviceId="verify-jellyfin-accounts", Version="1"';
const sha256 = bytes => createHash("sha256").update(bytes).digest("hex");

/** A 32x32 PNG of one colour, so each avatar change has distinct bytes. */
function solidPng([red, green, blue]) {
  const chunk = (type, data) => {
    const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type), data]);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(body));
    return Buffer.concat([length, body, crc]);
  };
  const header = Buffer.alloc(13); header.writeUInt32BE(32, 0); header.writeUInt32BE(32, 4); header[8] = 8; header[9] = 2;
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array.from({ length: 32 }, () => [red, green, blue]).flat())]);
  return Buffer.concat([Buffer.from("89504e470d0a1a0a", "hex"), chunk("IHDR", header), chunk("IDAT", zlib.deflateSync(Buffer.concat(Array(32).fill(row)))), chunk("IEND", Buffer.alloc(0))]);
}

/**
 * Write a database at JF-04's last revision with import-only admission stored, as
 * the parent stored it, so this run's startup upgrade is plan lane 2.
 */
export function seedImportOnlyDatabase(run) {
  fs.mkdirSync(run.data, { mode: 0o700 });
  execFileSync(pythonPath(run.root), ["-P", "-c", IMPORT_ONLY, path.join(run.data, "scanner.db")], {
    cwd: run.root, env: childEnvironment(run.root, path.join(run.scratch, "fixture-data")), encoding: "utf8", timeout: 60000,
  });
}

/**
 * JF-05 Jellyfin-owned accounts on the run's Jellyfin 12: every first sign-in
 * creates an account whose starting role follows Jellyfin administrator status,
 * the owner alone changes it afterwards, Jellyfin owns the username, name, and
 * avatar, import still works, and Jellyfin disablement ends the session. The run
 * starts from `seedImportOnlyDatabase`, so the first sign-in follows the upgrade.
 * Steps are the plan's lanes 1 to 10; step 11 is an extra check; steps 12 to 15
 * cover the fix round's username refresh and deletion with Allow them back or Block them.
 */
export async function verifyJellyfinAccounts(run, owner) {
  await launchBrowser(run);
  const ui = usersUI(run);
  const { expect, secret, api, status, open, signIn, details } = ui;
  const directory = async page => { await ui.directory(page); await page.reload(); await expect(page.getByRole("table")).toBeVisible(); };
  const origin = run.manifest.origin;
  const jellyfin = run.jellyfin;
  const steps = [];
  const step = (number, name, detail = {}) => {
    steps.push({ step: number, name, ...detail });
    run.write("jellyfin-accounts-steps.json", steps);
    console.log(`PASS ${number}: ${name}`);
  };
  const shot = (page, name) => capture(page, run, name).then(() => `${name}.png`);

  const adminToken = (await (await fetch(`${jellyfin.base}/Users/AuthenticateByName`, { method: "POST", headers: { Authorization: CLIENT, "Content-Type": "application/json" }, body: JSON.stringify({ Username: jellyfin.admin.name, Pw: jellyfin.admin.password }) })).json()).AccessToken;
  run.secrets.add(adminToken);
  const asAdmin = { Authorization: `${CLIENT}, Token="${adminToken}"` };
  async function createJellyfinUser(name, administrator) {
    const password = secret();
    const { Id: id } = await jellyfin.call("POST", "/Users/New", { Name: name, Password: password });
    if (administrator) await setPolicy(id, { IsAdministrator: true });
    return { id, name, password };
  }
  async function setPolicy(id, change) {
    const { Policy } = await jellyfin.call("GET", `/Users/${id}`);
    await jellyfin.call("POST", `/Users/${id}/Policy`, { ...Policy, ...change });
  }
  async function renameJellyfinUser(user, name) {
    const dto = await jellyfin.call("GET", `/Users/${user.id}`);
    const response = await fetch(`${jellyfin.base}/Users?userId=${user.id}`, { method: "POST", headers: { ...asAdmin, "Content-Type": "application/json" }, body: JSON.stringify({ ...dto, Name: name }) });
    assert.ok(response.ok, `Jellyfin renamed ${user.name} to ${name}: ${response.status}`);
    user.name = name;
  }
  async function setJellyfinAvatar(user, png) {
    const response = await fetch(`${jellyfin.base}/UserImage?userId=${user.id}`, { method: "POST", headers: { ...asAdmin, "Content-Type": "image/png" }, body: png.toString("base64") });
    assert.ok(response.ok, `Jellyfin stored a new avatar for ${user.name}: ${response.status}`);
  }
  const findAccount = async (context, name) => (await api(context, "GET", `/api/users?search=${encodeURIComponent(name)}`)).items.find(account => account.source === "jellyfin" && account.display_name === name);
  async function jellyfinSignIn(user, width = 1440) {
    const actor = await open(width);
    await signIn(actor, user.name, user.password);
    await expect(actor.page.getByRole("heading", { name: "My profile", exact: true })).toBeVisible();
    return { ...actor, session: await status(actor.context) };
  }
  async function avatarOf(context, id) {
    const response = await context.request.get(`${origin}/api/users/${id}/avatar`);
    assert.equal(response.status(), 200, `GET /api/users/${id}/avatar`);
    return sha256(await response.body());
  }
  const roleCell = (page, name) => page.getByRole("row").filter({ has: page.getByRole("button", { name: `View ${name}`, exact: true }) }).getByRole("cell").nth(3);
  async function syncFromUsers(page) {
    await directory(page);
    const synced = page.waitForResponse(response => new URL(response.url()).pathname === "/api/jellyfin/sync" && response.request().method() === "POST");
    await page.getByRole("button", { name: "Sync Jellyfin now", exact: true }).click();
    assert.equal((await synced).status(), 200, "Users > Sync Jellyfin now");
    await expect(page.getByRole("region", { name: "Sync results" })).toBeVisible();
  }

  try {
    const viewer = await createJellyfinUser("jf05-viewer", false);
    const boss = await createJellyfinUser("jf05-boss", true);
    const chief = await createJellyfinUser("jf05-chief", true);
    const ownerUI = await open(1440);
    await signIn(ownerUI, owner.username, owner.password, "users");
    await directory(ownerUI.page);
    assert.equal(await findAccount(ownerUI.context, viewer.name), undefined, "jf05-viewer has no account before signing in");
    assert.equal(await findAccount(ownerUI.context, boss.name), undefined, "jf05-boss has no account before signing in");

    const viewerUI = await jellyfinSignIn(viewer);
    const viewerId = viewerUI.session.access.principal.user_id;
    assert.equal(viewerUI.session.access.principal.role, "user", "first sign-in made the ordinary Jellyfin user an Editarr user");
    const viewerAccount = await api(ownerUI.context, "GET", `/api/users/${viewerId}`);
    assert.deepEqual([viewerAccount.source, viewerAccount.role, viewerAccount.display_name], ["jellyfin", "user", viewer.name], "the owner reads the new linked user account");
    step(1, "first Jellyfin sign-in of an ordinary user creates an Editarr user", { screenshot: await shot(viewerUI.page, "jf05-first-signin") });

    const journal = JSON.parse(fs.readFileSync(path.join(run.data, "migration-upgrade.json"), "utf8"));
    assert.deepEqual([journal.status, journal.from_revision], ["complete", PARENT_REVISION], "startup upgraded the import-only parent database");
    const history = await api(ownerUI.context, "GET", "/api/audit?action=settings.jellyfin_admission.change&page=1&page_size=50");
    const migrated = history.events.filter(event => event.action === "settings.jellyfin_admission.change");
    assert.deepEqual(migrated.map(event => [event.actor_kind, event.details]), [["system", { mode: "first_login", previous: "import_only" }]], "the audit history records the switch from import-only once");
    await ownerUI.page.goto(`${origin}/#/audit/${migrated[0].event_id}`);
    await expect(ownerUI.page.getByText(/import_only|import-only/i).first()).toBeVisible();
    step(2, "an import-only parent database upgrades, a new Jellyfin user signs in, and the audit history records the change", { journal: { from: journal.from_revision, to: journal.to_revision }, event: migrated[0].event_id, screenshot: await shot(ownerUI.page, "jf05-mode-migrated") });

    const bossUI = await jellyfinSignIn(boss);
    const bossId = bossUI.session.access.principal.user_id;
    assert.equal(bossUI.session.access.principal.role, "admin", "first sign-in made the Jellyfin administrator an Editarr admin");
    await directory(ownerUI.page);
    await expect(roleCell(ownerUI.page, boss.name)).toHaveText("admin");
    step(3, "first Jellyfin sign-in of an administrator creates an Editarr admin shown as admin in Users", { screenshot: await shot(ownerUI.page, "jf05-admin-default") });

    const dialog = await details(ownerUI.page, boss.name);
    await dialog.locator("summary").filter({ hasText: /^Role and permissions$/ }).click();
    await dialog.getByRole("combobox", { name: "Assigned role", exact: true }).selectOption("user");
    await dialog.getByRole("button", { name: "Assign role", exact: true }).click();
    const proof = ownerUI.page.getByRole("dialog", { name: "Assign account role", exact: true });
    await proof.getByLabel(/^(?:Current|Jellyfin) password$/).fill(owner.password);
    const assigned = ownerUI.page.waitForResponse(response => new URL(response.url()).pathname === `/api/users/${bossId}/role` && response.request().method() === "PUT");
    await proof.getByRole("button", { name: "Confirm change", exact: true }).click();
    assert.equal((await assigned).status(), 200, "owner role change with fresh Jellyfin password proof");
    await expect(proof).toHaveCount(0);
    await expect(dialog.locator(".account-facts dd").first()).toHaveText("user");
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    const bossAgain = await jellyfinSignIn(boss);
    assert.equal(bossAgain.session.access.principal.role, "user", "a later sign-in by the Jellyfin administrator keeps the owner's role");
    await syncFromUsers(ownerUI.page);
    assert.equal((await api(ownerUI.context, "GET", `/api/users/${bossId}`)).role, "user", "a manual sync keeps the owner's role");
    await expect(roleCell(ownerUI.page, boss.name)).toHaveText("user");
    step(4, "owner's role change to user survives the administrator's next sign-in and a manual sync", { screenshot: await shot(ownerUI.page, "jf05-role-change") });

    await renameJellyfinUser(viewer, "jf05-renamed");
    await syncFromUsers(ownerUI.page);
    const renamed = await api(ownerUI.context, "GET", `/api/users/${viewerId}`);
    assert.deepEqual([renamed.display_name, renamed.username], [viewer.name, viewer.name], "sync took the new Jellyfin name as display name and username");
    await expect(ownerUI.page.getByRole("button", { name: `View ${viewer.name}`, exact: true })).toBeVisible();
    await shot(ownerUI.page, "jf05-rename-users");
    await viewerUI.page.reload();
    const identity = viewerUI.page.locator(".account-fields > div").filter({ has: viewerUI.page.getByText("Jellyfin username", { exact: true }) });
    await expect(identity.locator("p")).toHaveText(viewer.name);
    const usernameCell = ownerUI.page.getByRole("row").filter({ has: ownerUI.page.getByRole("button", { name: `View ${viewer.name}`, exact: true }) }).getByRole("cell").nth(1);
    await expect(usernameCell).toHaveText(viewer.name);
    step(5, "a Jellyfin rename reaches Users and the user's profile after sync", { screenshot: await shot(viewerUI.page, "jf05-rename"), also: "jf05-rename-users.png" });

    const digests = [];
    for (const colour of [[200, 30, 30], [30, 60, 200]]) {
      await setJellyfinAvatar(viewer, solidPng(colour));
      const signedIn = await jellyfinSignIn(viewer);
      await expect(signedIn.page.locator(".profile-editor .account-avatar")).toHaveJSProperty("complete", true);
      const account = await api(signedIn.context, "GET", `/api/users/${viewerId}`);
      assert.ok(account.avatar, "the signed-in account has a Jellyfin avatar");
      digests.push({ digest: account.avatar, image: await avatarOf(signedIn.context, viewerId), page: signedIn.page });
    }
    assert.notEqual(digests[1].digest, digests[0].digest, "the avatar digest changed after the Jellyfin avatar changed");
    assert.notEqual(digests[1].image, digests[0].image, "the served avatar image changed after the Jellyfin avatar changed");
    step(6, "a new Jellyfin avatar replaces the Editarr avatar digest and image at the next sign-in", { digests: digests.map(({ digest, image }) => ({ digest, image })), screenshot: await shot(digests[1].page, "jf05-avatar") });

    const self = digests[1].page.context();
    assert.equal((await api(self, "PATCH", `/api/users/${viewerId}`, { display_name: "Local Name" }, 409)).code, "linked_profile_field", "PATCH display_name is refused");
    await api(self, "PATCH", `/api/users/${viewerId}`, { username: "jf05-local-name" }, 422);
    assert.equal((await api(self, "PUT", `/api/users/${viewerId}/avatar`, solidPng([0, 160, 0]), 409)).code, "linked_profile_field", "PUT avatar is refused");
    assert.deepEqual((await api(self, "GET", `/api/users/${viewerId}`)).read_only_fields, ["username", "display_name", "avatar"]);
    const profile = digests[1].page;
    await profile.goto(`${origin}/#/settings/profile`);
    await expect(profile.getByText("Avatar from Jellyfin", { exact: true })).toBeVisible();
    for (const label of ["Username", "Display name", "Avatar image"]) assert.equal(await profile.getByLabel(label, { exact: true }).count(), 0, `the profile has no ${label} control`);
    for (const name of ["Upload avatar", "Remove avatar", "Save username"]) assert.equal(await profile.getByRole("button", { name, exact: true }).count(), 0, `the profile has no ${name} button`);
    await expect(profile.getByLabel("Email", { exact: true })).toBeEditable();
    await expect(profile.getByLabel("Timezone", { exact: true })).toBeEditable();
    step(7, "linked username, display name, and avatar edits are refused 409 and the profile offers no control for them", { screenshot: await shot(profile, "jf05-profile-readonly") });

    await directory(ownerUI.page);
    await ownerUI.page.getByRole("button", { name: "Import Jellyfin users", exact: true }).click();
    const importing = ownerUI.page.getByRole("dialog", { name: "Import Jellyfin users", exact: true });
    await importing.getByRole("checkbox", { name: `${chief.name} (administrator)`, exact: true }).check();
    await importing.getByRole("button", { name: "Import selected", exact: true }).click();
    await expect(importing.getByRole("region", { name: "Import results" })).toContainText(`${chief.name}: imported`);
    const importShot = await shot(ownerUI.page, "jf05-import");
    await importing.getByRole("button", { name: "Close", exact: true }).click();
    const imported = await findAccount(ownerUI.context, chief.name);
    assert.ok(imported, "the imported Jellyfin administrator has an account");
    assert.deepEqual([imported.role, imported.status, imported.last_login_at], ["admin", "active", null], "import made an admin account before any sign-in");
    await expect(roleCell(ownerUI.page, chief.name)).toHaveText("admin");
    step(8, "owner imports a Jellyfin administrator who has never signed in and gets an Editarr admin", { screenshot: importShot });

    await ownerUI.page.setViewportSize({ width: 320, height: 900 });
    await directory(ownerUI.page);
    assert.equal(await ownerUI.page.getByLabel("Jellyfin admission").count(), 0, "no Jellyfin admission control in Users");
    assert.equal(await ownerUI.page.getByText("Jellyfin admission").count(), 0, "no Jellyfin admission text in Users");
    const usersNarrow = await shot(ownerUI.page, "jf05-users-320");
    await profile.setViewportSize({ width: 320, height: 900 });
    await profile.reload();
    await expect(profile.getByText("Avatar from Jellyfin", { exact: true })).toBeVisible();
    step(9, "Users and the linked profile fit 320px with no admission control", { screenshots: [usersNarrow, await shot(profile, "jf05-profile-320")] });

    assert.equal((await status(self)).state, "authenticated", "the linked user is signed in before Jellyfin disables them");
    await setPolicy(viewer.id, { IsDisabled: true });
    await ownerUI.page.setViewportSize({ width: 1440, height: 900 });
    await syncFromUsers(ownerUI.page);
    await expect(ownerUI.page.getByRole("region", { name: "Sync results" })).toContainText(viewer.name);
    assert.equal((await api(ownerUI.context, "GET", `/api/users/${viewerId}`)).status, "disabled", "sync disabled the account Jellyfin disabled");
    assert.equal((await status(self)).state, "unauthenticated", "the disabled user's session ended");
    await profile.reload();
    await expect(profile.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeVisible();
    await directory(ownerUI.page);
    step(10, "a Jellyfin-disabled user is disabled at sync and signed out", { screenshot: await shot(ownerUI.page, "jf05-disabled") });

    const admission = await ownerUI.context.request.get(`${origin}/api/settings/jellyfin-admission`);
    assert.equal(admission.status(), 404, "GET /api/settings/jellyfin-admission is gone");
    await api(ownerUI.context, "POST", `/api/users/${bossId}/role-sync`, undefined, 404);
    step(11, "extra check: the admission setting and role-sync routes answer 404", { statuses: { admission: admission.status(), roleSync: 404 } });

    const holder = await createJellyfinUser("jf05-taken", false);
    const holderUI = await jellyfinSignIn(holder);
    await holderUI.context.close();
    await renameJellyfinUser(holder, "jf05-holder");
    const claimant = await createJellyfinUser("jf05-taken", false);
    const claimantUI = await jellyfinSignIn(claimant);
    const claimantId = claimantUI.session.access.principal.user_id;
    assert.equal((await api(ownerUI.context, "GET", `/api/users/${claimantId}`)).username, null, "the holder keeps the name until a sync renames it; the new linked account has none yet");
    const held = await api(ownerUI.context, "GET", `/api/audit?action=account.jellyfin.state&target_user_id=${claimantId}&page=1&page_size=50`);
    assert.deepEqual(held.events.map(event => event.details.username), ["unavailable"], "the held name is audited once");
    await directory(ownerUI.page);
    const heldRows = ownerUI.page.getByRole("row").filter({ has: ownerUI.page.getByRole("button", { name: `View ${claimant.name}`, exact: true }) });
    await expect(heldRows).toHaveCount(2);
    const heldCells = (await heldRows.evaluateAll(rows => rows.map(row => [...row.querySelectorAll("td")].map(cell => cell.textContent)))).map(cells => [cells[1], cells[2]]).sort();
    assert.deepEqual(heldCells, [["None (Jellyfin name: jf05-taken)", "Jellyfin"], ["jf05-taken", "Jellyfin"]], "Users shows only the holder with the held username");
    const heldShot = await shot(ownerUI.page, "jf05-username-held");
    // One sync renames the holder; the claimant takes the released name then or at the next sync.
    await syncFromUsers(ownerUI.page);
    await syncFromUsers(ownerUI.page);
    assert.equal((await api(ownerUI.context, "GET", `/api/users/${claimantId}`)).username, claimant.name, "the next sync takes the released name");
    await renameJellyfinUser(claimant, "jf05-claimed");
    assert.equal((await jellyfinSignIn(claimant)).session.access.principal.user_id, claimantId);
    assert.equal((await api(ownerUI.context, "GET", `/api/users/${claimantId}`)).username, claimant.name, "a sign-in takes Jellyfin's new name as the username");
    await directory(ownerUI.page);
    const claimedRow = ownerUI.page.getByRole("row").filter({ has: ownerUI.page.getByRole("button", { name: `View ${claimant.name}`, exact: true }) });
    await expect(claimedRow.getByRole("cell").nth(1)).toHaveText(claimant.name);
    step(12, "fix round: a name held by another account waits until a sync renames the holder, shows as None with its Jellyfin name, is audited once, and is taken at the next sync; a Jellyfin rename reaches the username at sign-in", { screenshots: [heldShot, await shot(ownerUI.page, "jf05-username-refresh")] });

    async function deleteFromUsers(page, name, choice) {
      await directory(page);
      await page.getByRole("button", { name: `Delete ${name}`, exact: true }).click();
      const dialog = page.getByRole("dialog", { name: `Delete ${name}`, exact: true });
      await dialog.getByRole("radio", { name: /Keep data/ }).check();
      await dialog.getByRole("button", { name: "Review impact", exact: true }).click();
      const jellyfinChoice = dialog.getByRole("group", { name: "After deletion, their Jellyfin user", exact: true });
      await expect(jellyfinChoice.getByRole("radio", { name: /Allow them back/ })).toBeChecked();
      await jellyfinChoice.getByRole("radio", { name: choice === "block" ? /Block them/ : /Allow them back/ }).check();
      const captured = await shot(page, `jf05-delete-${choice}-${page.viewportSize().width}`);
      await dialog.getByRole("button", { name: "Delete account", exact: true }).click();
      const confirm = page.getByRole("dialog", { name: `Confirm deleting ${name}`, exact: true });
      await confirm.getByLabel(/^(?:Current|Jellyfin) password$/).fill(owner.password);
      const deleted = page.waitForResponse(response => /\/api\/users\/\d+\/deletion$/.test(new URL(response.url()).pathname) && response.request().method() === "POST");
      await confirm.getByRole("button", { name: "Delete account", exact: true }).click();
      const answer = await deleted;
      assert.equal(answer.status(), 200, `delete ${name} with ${choice}`);
      await dialog.getByRole("button", { name: "Done", exact: true }).click();
      return { result: await answer.json(), screenshot: captured };
    }

    const returner = await createJellyfinUser("jf05-returner", false);
    const firstReturn = (await jellyfinSignIn(returner)).session.access.principal.user_id;
    const allowed = await deleteFromUsers(ownerUI.page, returner.name, "allow");
    assert.deepEqual([allowed.result.identity_released, allowed.result.jellyfin_blocked], [true, false]);
    const secondReturn = (await jellyfinSignIn(returner)).session.access.principal.user_id;
    assert.notEqual(secondReturn, firstReturn, "the next sign-in created a fresh account");
    step(13, "fix round: deleting with Allow them back lets the next Jellyfin sign-in create a fresh account", { accounts: [firstReturn, secondReturn], screenshot: allowed.screenshot });

    const banned = await createJellyfinUser("jf05-banned", false);
    await jellyfinSignIn(banned);
    await ownerUI.page.setViewportSize({ width: 320, height: 900 });
    const blocked = await deleteFromUsers(ownerUI.page, banned.name, "block");
    assert.deepEqual([blocked.result.identity_released, blocked.result.jellyfin_blocked], [true, true]);
    const refusedUI = await open(1440);
    await signIn(refusedUI, banned.name, banned.password, true);
    await expect(refusedUI.page.getByRole("alert")).toContainText("This Jellyfin user is blocked from Editarr. Ask the owner to unblock you.");
    const refusedShot = await shot(refusedUI.page, "jf05-blocked-signin");
    const configuration = await jellyfin.call("GET", "/System/Configuration");
    await jellyfin.call("POST", "/System/Configuration", { ...configuration, QuickConnectAvailable: true });
    const bannedToken = (await (await fetch(`${jellyfin.base}/Users/AuthenticateByName`, { method: "POST", headers: { Authorization: CLIENT, "Content-Type": "application/json" }, body: JSON.stringify({ Username: banned.name, Pw: banned.password }) })).json()).AccessToken;
    run.secrets.add(bannedToken);
    const qc = refusedUI.context.request;
    const QC = `${origin}/api/auth/jellyfin/quick-connect`;
    const started = await qc.post(`${QC}/initiate`, { headers: { Origin: origin } });
    assert.equal(started.status(), 201, "Quick Connect code issued");
    const { code } = await started.json();
    const authorized = await fetch(`${jellyfin.base}/QuickConnect/Authorize?code=${code}`, { method: "POST", headers: { Authorization: `${CLIENT}, Token="${bannedToken}"` } });
    assert.ok(authorized.ok, `Jellyfin approved the code: ${authorized.status}`);
    await expect.poll(async () => (await (await qc.get(`${QC}/status`, { headers: { Origin: origin } })).json()).state, { timeout: 15000, intervals: [1100] }).toBe("approved");
    const quick = await qc.post(`${QC}/complete`, { headers: { Origin: origin }, data: { remember_me: false } });
    assert.deepEqual([quick.status(), (await quick.json()).code], [403, "jellyfin_user_blocked"], "Quick Connect refuses the blocked user");
    const reimport = await api(ownerUI.context, "POST", "/api/jellyfin/users/import", { user_ids: [banned.id.replaceAll("-", "").toLowerCase()] });
    assert.equal(reimport.results[0].outcome, "blocked", "import refuses the blocked user");
    const denials = await api(ownerUI.context, "GET", "/api/audit?action=auth.sign_in&outcome=denied&page=1&page_size=50");
    assert.equal(denials.events.filter(event => event.details.reason === "jellyfin_user_blocked").length, 2, "password and Quick Connect refusals are audited");
    const ownerId = (await status(ownerUI.context)).access.principal.user_id;
    const proven = await api(ownerUI.context, "POST", `/api/users/${ownerId}/link/proof`, { reauthentication: { password: owner.password }, jellyfin: { username: banned.name, password: banned.password } });
    assert.ok(proven.preview.blockers.includes("jellyfin_user_blocked"), "the link preview names the block");
    const linked = await api(ownerUI.context, "POST", `/api/users/${ownerId}/link`, { reauthentication: { password: owner.password }, proof: proven.proof, confirmation: proven.confirmation }, 409);
    assert.equal(linked.code, "link_blocked", "linking the blocked user is refused");
    const linkEvents = (await api(ownerUI.context, "GET", `/api/audit?action=account.link&target_user_id=${ownerId}&page=1&page_size=50`)).events;
    const refusals = linkEvents.filter(event => event.outcome === "denied" && event.details.reason === "link_blocked").map(event => event.details.step).sort();
    assert.deepEqual(refusals, ["link", "proof"], "the preview refusal and the refused link are both audited");
    const proofRefusal = linkEvents.find(event => event.outcome === "denied" && event.details.step === "proof");
    await ownerUI.page.setViewportSize({ width: 1440, height: 900 });
    await ownerUI.page.goto(`${origin}/#/audit/${proofRefusal.event_id}`);
    await expect(ownerUI.page.getByText("link_blocked").first()).toBeVisible();
    const linkAuditShot = await shot(ownerUI.page, "jf05-link-refused-audit");
    step("14a", "fix round: linking a blocked Jellyfin user is refused at preview and at link, and audit history shows the preview refusal", { events: linkEvents.map(event => [event.outcome, event.details.step, event.details.reason ?? null]), screenshot: linkAuditShot });
    await ownerUI.page.setViewportSize({ width: 320, height: 900 });
    await directory(ownerUI.page);
    const blockList = ownerUI.page.getByRole("region", { name: "Blocked Jellyfin users", exact: true });
    await expect(blockList).toContainText(banned.name);
    await blockList.scrollIntoViewIfNeeded();
    step(14, "fix round: deleting with Block them refuses password sign-in, Quick Connect, and import, audits each, and lists the user in Users at 320px", { screenshots: [blocked.screenshot, refusedShot, await shot(ownerUI.page, "jf05-users-blocked-320")], quickConnect: quick.status() });

    await blockList.getByRole("button", { name: `Unblock ${banned.name}`, exact: true }).click();
    const unblock = ownerUI.page.getByRole("dialog", { name: `Unblock ${banned.name}`, exact: true });
    await unblock.getByLabel(/^(?:Current|Jellyfin) password$/).fill(owner.password);
    await unblock.getByRole("button", { name: "Unblock", exact: true }).click();
    await expect(blockList.getByRole("status")).toHaveText(`${banned.name} is unblocked.`);
    const unblockedShot = await shot(ownerUI.page, "jf05-unblocked-320");
    await ownerUI.page.setViewportSize({ width: 1440, height: 900 });
    const back = await jellyfinSignIn(banned);
    assert.equal(back.session.access.principal.role, "user", "the unblocked user signs in to a fresh account");
    step(15, "fix round: the owner unblocks with a fresh password and the user's next sign-in creates a fresh account", { screenshot: unblockedShot });

    assert.deepEqual(ui.errors, [], "no browser page errors");
    run.write("jellyfin-accounts-result.json", { passed: true, feature: "jf-05-jellyfin-accounts", jellyfin: jellyfin.image, revision: run.manifest.revision, steps });
    console.log("PASS: an import-only database upgrades and records the change, usernames follow Jellyfin with held names waiting, deletion allows back or blocks with unblock, Jellyfin first sign-in creates user and admin accounts, the owner's role change outlasts sign-in and sync, Jellyfin renames, avatars, and disablement reach Editarr, linked profile edits are refused, import works, 320px fits, and admission and role-sync routes are gone.");
  } catch (error) {
    for (const [index, context] of ui.contexts.entries()) for (const page of context.pages()) await capture(page, run, `jf05-failure-${index}`).catch(() => {});
    throw error;
  } finally { await ui.close(); }
}
