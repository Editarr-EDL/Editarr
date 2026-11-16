import assert from "node:assert/strict";
import fs from "node:fs";
import { bothWidths, choose, confirmDeletion, findAccount, holdPreview, nameOf, tabTo, unclipped } from "./account-deletion-support.mjs";
import { identityOf, runJellyfinUsers } from "./account-link-support.mjs";
import { query } from "./database.mjs";
import { renameJellyfinUser } from "./run-jellyfin.mjs";

export async function verifyProtectedTargets(context) {
  const { run, ui, member, ownerId, call, browser, origin } = context;
  const admin = await member("ui-admin", "admin");
  const [{ id: otherAdmin }] = query(run, "SELECT user_id AS id FROM user_profiles WHERE display_name = 'deleting-admin'");
  const ownerName = nameOf(await call(browser.context, "GET", `/api/users/${ownerId}`));
  const view = await ui.open(1440);
  await ui.signIn(view, admin.username, admin.password, "users");
  const adminContext = { ...context, browser: view };
  for (const [search, name] of [[ownerName, ownerName], ["deleting-admin", "deleting-admin"], ["ui-admin", "ui-admin"]]) {
    await findAccount(adminContext, search, name);
    assert.equal(await view.page.getByRole("button", { name: `Delete ${name}`, exact: true }).count(), 0, `no row deletion for ${name}`);
    assert.equal(await view.page.getByRole("checkbox", { name: `Select ${name}`, exact: true }).count(), 0, `no bulk selection for ${name}`);
  }
  const before = ui.requests.length;
  const refusals = [[ownerId, `${ownerName} is the owner and can't be deleted.`], [otherAdmin, "deleting-admin is an admin. Only the owner can delete an admin account."], [admin.id, "You can't delete your own account."]];
  for (const [id, message] of refusals) {
    await view.page.goto(`${origin}/#/settings/users/${id}/delete`);
    await ui.expect(view.page.getByText(message, { exact: true })).toBeVisible();
    assert.equal(await view.page.getByRole("dialog").count(), 0);
  }
  await ui.evidence(view.page, "protected-deep-link-1440");
  await view.page.setViewportSize({ width: 320, height: 900 });
  await ui.evidence(view.page, "protected-deep-link-320");
  assert.deepEqual(ui.requests.slice(before).filter(request => request.path.includes("/deletion")), [], "no deletion preview for a protected deep link");
  const forged = await call(admin.context, "POST", "/api/users/bulk-deletion", { data: { targets: [ownerId, otherAdmin].map(user_id => ({ user_id, mode: "retain", confirmation: "0".repeat(64) })), reauthentication: { password: admin.password } } });
  assert.deepEqual(forged.results.map(result => [result.user_id, result.outcome, result.code]), [[ownerId, "refused", "protected_account"], [otherAdmin, "refused", "protected_account"]]);
  for (const id of [ownerId, otherAdmin]) assert.equal(context.account(id).status, "active");
  ui.record("an admin sees no row or bulk deletion for the owner, another admin, or itself; their deep links explain the protection without a preview; a forged bulk request is refused protected_account");
  return { admin: admin.id, otherAdmin, deepLinkMessages: refusals.map(([, message]) => message), forgedBulk: forged.results.map(result => [result.user_id, result.code]) };
}

export async function verifyRetention(context) {
  const { ui, browser, member, publish, importReview, files, account, call } = context;
  const { page } = browser;
  const retained = await member("ui-retained");
  const kept = await publish(retained, "UI retained review");
  const ids = [kept.ids.edl, kept.ids.mcf];
  const before = files(ids);
  await findAccount(context, "ui-retained", "ui-retained");
  await tabTo(page, page.getByRole("button", { name: "Delete ui-retained", exact: true }));
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Delete ui-retained", exact: true });
  await ui.checkTrap(page, dialog);
  const keep = dialog.getByRole("radio", { name: /Keep data/ });
  await tabTo(page, keep);
  await page.keyboard.press("Space");
  await ui.expect(keep).toBeChecked();
  await tabTo(page, dialog.getByRole("button", { name: "Review impact", exact: true }));
  await page.keyboard.press("Enter");
  const impact = dialog.getByRole("region", { name: "Deletion impact", exact: true });
  await ui.expect(impact).toContainText("1 saved review stays unchanged, with its scans and filter preferences.");
  await ui.expect(impact).toContainText("2 personal export files stay published exactly as they are. Media players keep using them.");
  await ui.expect(impact).toContainText(/ui-retained can no longer sign in: \d+ browser sessions? ends?, its Jellyfin link is released\./);
  await ui.checkTrap(page, dialog);
  const checked = await bothWidths(context, dialog, "retain-review");
  await importReview(retained, "UI created after preview");
  await tabTo(page, dialog.getByRole("button", { name: "Delete account", exact: true }));
  await page.keyboard.press("Enter");
  const path = `/api/users/${retained.id}/deletion`;
  const stale = await confirmDeletion(context, "Confirm deleting ui-retained", path);
  assert.deepEqual([stale.status, stale.body?.detail?.code ?? stale.body?.code], [409, "stale_account_deletion"]);
  await ui.expect(impact).toContainText("2 saved reviews stay unchanged");
  await ui.expect(impact.getByRole("alert")).toHaveText("The account changed after this preview. Nothing changed. Review the current impact and confirm again.");
  assert.equal(account(retained.id).status, "active");
  await ui.evidence(page, "retain-stale-1440");
  await tabTo(page, dialog.getByRole("button", { name: "Delete account", exact: true }));
  await page.keyboard.press("Enter");
  const applied = await confirmDeletion(context, "Confirm deleting ui-retained", path);
  assert.equal(applied.status, 200);
  const result = dialog.getByRole("region", { name: "Deletion result", exact: true });
  await ui.expect(result).toContainText("2 reviews kept unchanged.");
  await ui.expect(result).toContainText("2 personal export files still published for this account.");
  await ui.evidence(page, "retain-result-1440");
  const after = files(ids);
  assert.deepEqual(after, before, "retained personal files keep their rows and bytes");
  for (const row of Object.values(after)) assert.equal(row.diskSha256, row.contentSha256);
  const state = account(retained.id);
  assert.deepEqual([state.status, state.linked, state.username, state.disposition.mode], ["disabled", false, null, "retain"]);
  await call(retained.context, "GET", `/api/users/${retained.id}`, {}, 401);
  await tabTo(page, dialog.getByRole("button", { name: "Done", exact: true }));
  await page.keyboard.press("Enter");
  await ui.expect(page.getByRole("region", { name: "Account deletion results", exact: true })).toContainText("ui-retained: deleted");
  await findAccount(context, "ui-retained");
  await ui.expect(page.getByText("No users found.", { exact: true })).toBeVisible();
  ui.record("retention by keyboard: impact reviewed, a review created after the preview refused 409 stale with nothing changed, the refreshed preview applied, both personal files kept byte for byte, and the account left the list");
  return { user: retained.id, stale: stale.body?.detail?.code ?? stale.body?.code, result: applied.body, before, after, account: state, controlsInside320: checked };
}

export async function verifyTransfer(context) {
  const { run, ui, browser, member, publish, files, account, origin } = context;
  const { page } = browser;
  const moving = await member("ui-transferred");
  const receiver = await member("ui-receiver");
  const moved = await publish(moving, "UI transferred review");
  const ids = [moved.ids.edl, moved.ids.mcf];
  const before = files(ids);
  const [{ local_export_id: receiverExport }] = query(run, "SELECT COALESCE((SELECT provider_user_id FROM user_provider_identities WHERE user_id = users.id), local_export_id) AS local_export_id FROM users WHERE id = ?", receiver.id);
  await page.goto(`${origin}/#/settings/users/${moving.id}/delete`);
  const dialog = page.getByRole("dialog", { name: "Delete ui-transferred", exact: true });
  await dialog.getByRole("radio", { name: /Transfer data/ }).check();
  await dialog.getByRole("searchbox", { name: "Find a destination account", exact: true }).fill("ui-receiver");
  await dialog.getByRole("button", { name: "Search accounts", exact: true }).click();
  const select = dialog.getByRole("combobox", { name: "Destination account", exact: true });
  await ui.expect(select.getByRole("option", { name: "ui-receiver", exact: true })).toHaveCount(1);
  await select.selectOption({ label: "ui-receiver" });
  await dialog.getByRole("button", { name: "Review impact", exact: true }).click();
  const impact = dialog.getByRole("region", { name: "Deletion impact", exact: true });
  await ui.expect(impact).toContainText("1 saved review moves to ui-receiver. ui-receiver becomes the owner; ui-transferred stays recorded as the original author.");
  await ui.expect(impact).toContainText("2 personal export files move to ui-receiver's export ID. 2 listed files follow ui-receiver's filter policy as the new owner of their reviews.");
  const listed = impact.getByRole("region", { name: "Published files of ui-transferred", exact: true }).getByRole("listitem");
  await ui.expect(listed).toHaveCount(2);
  for (const item of await listed.all()) await ui.expect(item).toContainText("moves to ui-receiver's export ID; follows ui-receiver's filter policy");
  const checked = await bothWidths(context, dialog, "transfer-review");
  await dialog.getByRole("button", { name: "Delete account", exact: true }).click();
  const applied = await confirmDeletion(context, "Confirm deleting ui-transferred", `/api/users/${moving.id}/deletion`);
  assert.equal(applied.status, 200, JSON.stringify(applied.body));
  await ui.expect(dialog.getByRole("region", { name: "Deletion result", exact: true })).toContainText("1 review transferred to ui-receiver. 2 personal export files moved.");
  await ui.evidence(page, "transfer-result-1440");
  const after = files(ids);
  for (const id of ids) {
    assert.deepEqual([after[id].target_user_id, after[id].target_export_id], [receiver.id, receiverExport]);
    assert.equal(after[id].diskSha256, before[id].diskSha256, "moved file keeps its bytes");
    assert.equal(fs.existsSync(before[id].path), false, "old path removed");
  }
  const [review] = query(run, "SELECT owner_id, original_author_id FROM saved_reviews WHERE id = ?", moved.review.id);
  assert.deepEqual([review.owner_id, review.original_author_id], [receiver.id, moving.id]);
  const state = account(moving.id);
  assert.deepEqual([state.status, state.disposition.mode, state.disposition.destination_id], ["disabled", "transfer", receiver.id]);
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  await ui.expect.poll(() => new URL(page.url()).hash).toMatch(/^#\/?settings\/users$/);
  ui.record("transfer from the deep link: destination searched and chosen, new owner and policy effect reviewed, both files moved to the destination's export ID with identical bytes, and the deep link cleared");
  return { user: moving.id, destination: receiver.id, destinationExportId: receiverExport, before, after, review: { ...review }, result: applied.body, controlsInside320: checked };
}

export async function verifyPermanentDeletion(context) {
  const { run, ui, browser, member, publish, files, account, call } = context;
  const { page } = browser;
  const removed = await member("ui-deleted");
  const doomed = await publish(removed, "UI deleted review");
  const detail = await call(browser.context, "GET", `/api/reviews/${doomed.review.id}`);
  const copy = await call(browser.context, "POST", `/api/reviews/${doomed.review.id}/duplicate`, { data: { revision: detail.revision, name: "UI owner copy keeps evidence", policy_owner_id: detail.policy_owner_id, policy_revision: detail.policy_revision } }, 201);
  const [{ scan_id: sharedScan }] = query(run, "SELECT scan_id FROM review_scan_memberships WHERE review_id = ?", doomed.review.id);
  const [{ media_item_id: mediaItem }] = query(run, "SELECT media_item_id FROM media_item_exports WHERE id = ?", doomed.ids.edl);
  const before = files([doomed.ids.edl, doomed.ids.mcf]);
  await page.setViewportSize({ width: 320, height: 900 });
  await findAccount(context, "ui-deleted", "ui-deleted");
  await page.getByRole("button", { name: "Delete ui-deleted", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Delete ui-deleted", exact: true });
  await choose(dialog, "Delete permanently");
  const impact = dialog.getByRole("region", { name: "Deletion impact", exact: true });
  const edl = impact.getByRole("group", { name: `EDL · ${mediaItem}`, exact: true });
  const mcf = impact.getByRole("group", { name: `MCF · ${mediaItem}`, exact: true });
  await ui.expect(edl.getByRole("radio", { name: "Keep file" })).toBeChecked();
  await ui.expect(mcf.getByRole("radio", { name: "Keep file" })).toBeChecked();
  await mcf.getByRole("radio", { name: "Remove file" }).check();
  await ui.expect(impact).toContainText("1 saved review is deleted.");
  await ui.expect(impact).toContainText("1 scan is kept because another account's review, a file outside this account's reviews, or pending community work needs it.");
  await ui.expect(impact).not.toContainText("deleted unless");
  await ui.expect(impact.getByRole("region", { name: "Scans of ui-deleted", exact: true })).toContainText(new RegExp(`Scan ${sharedScan.slice(0, 8)} \\(.+\\): kept, still needed`));
  const checked = await unclipped(page, dialog, 320);
  await ui.evidence(page, "delete-review-320");
  await dialog.getByRole("button", { name: "Delete account", exact: true }).click();
  const applied = await confirmDeletion(context, "Confirm deleting ui-deleted", `/api/users/${removed.id}/deletion`);
  assert.equal(applied.status, 200, JSON.stringify(applied.body));
  const result = dialog.getByRole("region", { name: "Deletion result", exact: true });
  await ui.expect(result).toContainText("1 review deleted. 1 published file kept, 1 removed.");
  await ui.expect(result).toContainText("0 scans deleted, 1 kept.");
  await unclipped(page, dialog, 320);
  await ui.evidence(page, "delete-result-320");
  await page.setViewportSize({ width: 1440, height: 900 });
  const after = files([doomed.ids.edl, doomed.ids.mcf]);
  assert.equal(after[doomed.ids.mcf], null);
  assert.equal(fs.existsSync(before[doomed.ids.mcf].path), false);
  assert.deepEqual(after[doomed.ids.edl], { ...before[doomed.ids.edl], review_id: null });
  assert.equal(query(run, "SELECT count(*) AS count FROM scans WHERE id = ?", sharedScan)[0].count, 1, "shared scan kept");
  assert.equal(query(run, "SELECT count(*) AS count FROM saved_reviews WHERE id = ?", copy.id)[0].count, 1, "owner copy survives");
  assert.equal(account(removed.id).disposition.mode, "delete");
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  ui.record("permanent deletion at 320 px: files kept by default, the MCF explicitly removed, the scan the owner's copy needs kept, and every control inside the viewport");
  return { user: removed.id, sharedScan, ownerCopy: copy.id, before, after, result: applied.body, controlsInside320: checked };
}

export async function verifyUnconfirmedDeletion(context) {
  const { run, ui, browser, member, publish, files, account, owner } = context;
  const { page } = browser;
  const target = await member("ui-unconfirmed");
  const published = await publish(target, "UI unconfirmed review");
  const [{ scan_id: scan }] = query(run, "SELECT scan_id FROM review_scan_memberships WHERE review_id = ?", published.review.id);
  const [{ media_item_id: mediaItem }] = query(run, "SELECT media_item_id FROM media_item_exports WHERE id = ?", published.ids.edl);
  await findAccount(context, "ui-unconfirmed", "ui-unconfirmed");
  await page.getByRole("button", { name: "Delete ui-unconfirmed", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Delete ui-unconfirmed", exact: true });
  await choose(dialog, "Delete permanently");
  const impact = dialog.getByRole("region", { name: "Deletion impact", exact: true });
  await ui.expect(impact.getByRole("group", { name: `EDL · ${mediaItem}`, exact: true }).getByRole("radio", { name: "Keep file" })).toBeChecked();
  await impact.getByRole("group", { name: `MCF · ${mediaItem}`, exact: true }).getByRole("radio", { name: "Remove file" }).check();
  await ui.expect(impact).toContainText("1 scan is deleted unless a published file that stays still uses it.");
  await ui.expect(impact.getByRole("region", { name: "Scans of ui-unconfirmed", exact: true })).toContainText(`Scan ${scan.slice(0, 8)}`);
  await ui.evidence(page, "unconfirmed-review-1440");
  const matches = url => url.pathname === `/api/users/${target.id}/deletion`;
  let server;
  const answered = new Promise(resolve => { server = resolve; });
  const drop = async route => {
    if (route.request().method() !== "POST") return route.fallback();
    const response = await route.fetch();
    server({ status: response.status(), body: await response.json() });
    await route.abort("connectionfailed");
  };
  await page.route(matches, drop);
  await dialog.getByRole("button", { name: "Delete account", exact: true }).click();
  const fresh = page.getByRole("dialog", { name: "Confirm deleting ui-unconfirmed", exact: true });
  await ui.checkTrap(page, fresh);
  await page.keyboard.type(owner.password);
  await page.keyboard.press("Enter");
  const applied = await answered;
  await page.unroute(matches, drop);
  assert.equal(applied.status, 200, JSON.stringify(applied.body));
  assert.deepEqual([applied.body.scans_deleted, applied.body.scans_kept], [[], [scan]], "the kept EDL keeps the account's own scan");
  await ui.expect(dialog.getByRole("region", { name: "Deletion stopped", exact: true })).toContainText("Editarr did not confirm whether ui-unconfirmed was deleted. Check the Users list before trying again.");
  await ui.expect(dialog).not.toContainText(/Nothing changed|Failed to fetch/);
  await ui.expect(page.getByRole("region", { name: "Account deletion results", exact: true })).toContainText("ui-unconfirmed: outcome unknown");
  await ui.evidence(page, "unconfirmed-result-1440");
  await dialog.getByRole("button", { name: "Close", exact: true }).first().click();
  await ui.expect(page.getByRole("button", { name: "View ui-unconfirmed", exact: true })).toHaveCount(0);
  await ui.expect(page.getByText("No users found.", { exact: true })).toBeVisible();
  assert.equal(account(target.id).disposition.mode, "delete");
  assert.equal(query(run, "SELECT count(*) AS count FROM scans WHERE id = ?", scan)[0].count, 1, "scan kept for the retained EDL");
  const after = files([published.ids.edl, published.ids.mcf]);
  assert.equal(after[published.ids.mcf], null);
  assert.equal(after[published.ids.edl].review_id, null);
  ui.record("permanent deletion whose answer never reached the browser: the scan a kept EDL uses was shown as conditional and kept, the dialog and results said the outcome is unknown instead of unchanged, and the reread Users list no longer showed the account");
  return { user: target.id, scan, server: applied.body };
}

export async function verifyBulkDeletion(context) {
  const { run, ui, browser, member, publish, importReview, account, call } = context;
  const { page } = browser;
  const [community] = query(run, "SELECT users.id FROM users JOIN user_profiles ON user_profiles.user_id = users.id WHERE display_name = 'mu32-member' AND status = 'active'");
  assert.ok(community, "the bulk step needs the community member that `--community` creates");
  const a = await member("zbulk-a");
  const b = await member("zbulk-b");
  await publish(a, "UI bulk A");
  await importReview(b, "UI bulk B");
  const [{ provider_user_id: communityJellyfinId }] = query(run, "SELECT provider_user_id FROM user_provider_identities WHERE user_id = ?", community.id);
  await renameJellyfinUser(run, communityJellyfinId, "zbulk-community");
  await call(browser.context, "POST", "/api/jellyfin/sync");
  const history = () => ({
    identity: query(run, "SELECT count(*) AS count FROM community_identities WHERE user_id = ?", community.id)[0].count,
    operations: query(run, "SELECT operation_id, state FROM community_operations WHERE owner_id = ? ORDER BY operation_id", community.id),
    publications: query(run, "SELECT count(*) AS count FROM community_publications p JOIN community_operations o ON o.operation_id = p.operation_id WHERE o.owner_id = ?", community.id)[0].count,
  });
  const historyBefore = history();
  assert.equal(historyBefore.identity, 1);
  assert.ok(historyBefore.operations.length > 0);
  await findAccount(context, "zbulk", "zbulk-a");
  for (const name of ["zbulk-community", "zbulk-a", "zbulk-b"]) await page.getByRole("checkbox", { name: `Select ${name}`, exact: true }).check();
  await page.getByRole("button", { name: "Delete selected", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Delete selected accounts", exact: true });
  await choose(dialog, "Keep data");
  const region = name => dialog.getByRole("region", { name: `Deletion of ${name}`, exact: true });
  for (const name of ["zbulk-community", "zbulk-a", "zbulk-b"]) await ui.expect(region(name)).toContainText(/stays? unchanged/);
  const pending = historyBefore.operations.filter(row => row.state === "pending").length;
  await ui.expect(region("zbulk-community")).toContainText(`The community identity and ${historyBefore.operations.length} community ${historyBefore.operations.length === 1 ? "record" : "records"} (${pending} pending) stay in place. Anything already shared with the community stays published; nothing is retracted, and Editarr does not contact the community service.`);
  const checked = await bothWidths(context, dialog, "bulk-review");
  const lateA = await holdPreview(page, a.id);
  await dialog.getByRole("button", { name: "Change choice", exact: true }).click();
  await choose(dialog, "Keep data");
  await lateA.requested;
  await ui.expect(region("zbulk-community")).toContainText("stays unchanged");
  await ui.expect(region("zbulk-a")).toContainText("Checking what this changes...");
  await ui.expect(dialog.getByRole("button", { name: /^Delete \d/ })).toHaveCount(0);
  await ui.evidence(page, "bulk-preview-pending-1440");
  lateA.release();
  for (const name of ["zbulk-a", "zbulk-b"]) await ui.expect(region(name)).toContainText(/stays? unchanged/);
  await importReview(b, "UI bulk stale");
  await dialog.getByRole("button", { name: "Delete 3 accounts", exact: true }).click();
  const first = await confirmDeletion(context, "Confirm deleting selected accounts", "/api/users/bulk-deletion");
  assert.equal(first.status, 200);
  const outcome = Object.fromEntries(first.body.results.map(result => [result.user_id, [result.outcome, result.code]]));
  assert.deepEqual(outcome, { [community.id]: ["applied", null], [a.id]: ["applied", null], [b.id]: ["refused", "stale_account_deletion"] });
  await ui.expect(region("zbulk-a")).toContainText("Deleted.");
  await ui.expect(region("zbulk-community")).toContainText("Community identity and");
  await ui.expect(region("zbulk-b").getByRole("alert")).toHaveText("The account changed after this preview. Nothing changed. Review the current impact and confirm again.");
  await ui.expect(region("zbulk-b")).toContainText("2 saved reviews stay unchanged");
  const results = page.getByRole("region", { name: "Account deletion results", exact: true });
  await ui.expect(results).toContainText("zbulk-a: deleted");
  await ui.expect(results).toContainText("zbulk-community: deleted");
  await ui.expect(results).toContainText("zbulk-b: not changed");
  assert.deepEqual([account(a.id).status, account(community.id).status, account(b.id).status], ["disabled", "disabled", "active"]);
  await ui.evidence(page, "bulk-partial-1440");
  await dialog.getByRole("button", { name: "Delete 1 account", exact: true }).click();
  const second = await confirmDeletion(context, "Confirm deleting selected accounts", "/api/users/bulk-deletion");
  assert.deepEqual(second.body.results.map(result => [result.user_id, result.outcome]), [[b.id, "applied"]]);
  await ui.expect(region("zbulk-b")).toContainText("Deleted.");
  await ui.expect(results).toContainText("zbulk-b: deleted");
  assert.equal(account(b.id).status, "disabled");
  const historyAfter = history();
  assert.deepEqual(historyAfter, historyBefore, "community identity, operations, and publications stay in place");
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  ui.record("bulk retention of three accounts: no confirmation while a delayed preview was still loading, then one stale target refused and re-reviewed while the other two applied, per-target results named, and the community member's identity, operations, and publications unchanged");
  return { targets: { community: community.id, a: a.id, b: b.id }, first: first.body.results.map(result => [result.user_id, result.outcome, result.code]), second: second.body.results.map(result => [result.user_id, result.outcome]), community: { before: historyBefore, after: historyAfter }, controlsInside320: checked };
}

/**
 * MU-39's duplicate-identity resolution, from the link dialog of `legacy-linker`, the
 * local password account the seeded 0029 database held and 0030 disabled with cause
 * `unlinked`: an imported Jellyfin user already holds the identity, the dialog offers
 * a reviewed deletion of that holder without offering the disabled linker as the
 * transfer destination, and linking restarts from a fresh proof.
 */
export async function verifyDuplicateLink(context) {
  const { run, ui, browser, owner, call, account } = context;
  const { page } = browser;
  const linker = run.legacyMembers.linker;
  assert.deepEqual([account(linker.id).status, identityOf(run, linker.id)], ["disabled", null], "0030 left the linker disabled and unlinked");
  const provider = await runJellyfinUsers(run, "Emery");
  const emery = provider.users.Emery;
  const imported = await call(browser.context, "POST", "/api/jellyfin/users/import", { data: { user_ids: [emery.userId] } });
  const holderId = imported.results[0].user_id;
  const holder = nameOf(await call(browser.context, "GET", `/api/users/${holderId}`));
  await findAccount(context, linker.name, linker.name);
  await page.getByRole("button", { name: `Link ${linker.name} to Jellyfin`, exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Link to Jellyfin", exact: true });
  const proof = { username: "Emery", password: emery.password, actorPassword: owner.password };
  const blocked = await ui.prove(page, dialog, proof);
  assert.deepEqual([blocked.status, blocked.body.preview.blockers, blocked.body.preview.status], [200, ["jellyfin_identity_in_use"], { current: "disabled", after: "active", cause: "unlinked" }]);
  const action = dialog.getByRole("button", { name: `Review deleting ${holder}`, exact: true });
  await ui.expect(action).toBeVisible();
  await ui.expect(dialog.getByText(`${linker.name} can't receive its data until it is linked.`)).toBeVisible();
  assert.equal(await dialog.getByRole("button", { name: "Confirm link", exact: true }).count(), 0);
  await ui.noUnlinkOrMerge(page);
  await bothWidths(context, dialog, "duplicate-blocker");
  await action.click();
  const deletion = page.getByRole("dialog", { name: `Delete ${holder}`, exact: true });
  await ui.checkTrap(page, deletion);
  await deletion.getByRole("radio", { name: /Transfer data/ }).check();
  const destinations = deletion.getByRole("combobox", { name: "Destination account", exact: true });
  await ui.expect(destinations).toHaveValue("");
  const transferToLinker = await destinations.getByRole("option", { name: linker.name, exact: true }).count();
  assert.equal(transferToLinker, 0, "the disabled link target is not offered as a destination");
  await ui.evidence(page, "duplicate-transfer-destinations-1440");
  await deletion.getByRole("radio", { name: /Keep data/ }).check();
  await deletion.getByRole("button", { name: "Review impact", exact: true }).click();
  const impact = deletion.getByRole("region", { name: "Deletion impact", exact: true });
  await ui.expect(impact).toContainText("its Jellyfin link is released");
  await ui.noUnlinkOrMerge(page);
  await ui.evidence(page, "duplicate-retain-1440");
  await deletion.getByRole("button", { name: "Delete account", exact: true }).click();
  const applied = await confirmDeletion(context, `Confirm deleting ${holder}`, `/api/users/${holderId}/deletion`);
  assert.equal(applied.status, 200, JSON.stringify(applied.body));
  assert.equal(applied.body.identity_released, true);
  await deletion.getByRole("button", { name: "Done", exact: true }).click();
  await ui.expect(dialog.getByRole("alert")).toHaveText(`${holder} was deleted. Prove the Jellyfin identity again to review a fresh link preview.`);
  assert.equal(identityOf(run, linker.id), null, "nothing linked before the fresh preview is confirmed");
  const again = await ui.prove(page, dialog, proof);
  assert.deepEqual([again.status, again.body.preview.blockers], [200, []]);
  const linked = await ui.confirm(page, dialog, owner.password);
  assert.equal(linked.status, 200, JSON.stringify(linked.body));
  const identity = identityOf(run, linker.id);
  assert.equal(identity.provider_user_id, emery.userId);
  assert.equal(account(linker.id).status, "active", "linking re-enabled the former local account");
  const state = account(holderId);
  assert.deepEqual([state.status, state.disposition.mode], ["disabled", "retain"]);
  assert.equal(query(run, "SELECT count(*) AS count FROM users WHERE id IN (?, ?) AND status = 'active'", linker.id, holderId)[0].count, 1, "two accounts remain two rows; no merge");
  await ui.noUnlinkOrMerge(page);
  await ui.evidence(page, "duplicate-linked-1440");
  ui.record("the duplicate-identity blocker on the 0030-disabled legacy-linker did not offer the disabled link target as a transfer destination, led to a reviewed retention of the imported holder, linking restarted from a fresh proof, and the link completed and re-enabled the account with no merge");
  return { linker: linker.id, holder: holderId, blocked: blocked.body.preview.blockers, preview: blocked.body.preview.status, deletion: applied.body, freshPreviewBlockers: again.body.preview.blockers, identity, holderAccount: state, transferToLinkerOffered: transferToLinker > 0 };
}
