import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { assertMoved, assertUnmoved, exportsOf, identityOf } from "./account-link-support.mjs";
import { query } from "./database.mjs";
import { itemRoute } from "./synthetic-jellyfin.mjs";

const accountState = (run, id) => query(run, "SELECT status, disable_cause, permission_revision FROM users WHERE id = ?", id)[0];

/**
 * The owner links `legacy-member`, a JF-05 local password account that migration 0030
 * disabled with cause `unlinked`, from the Users directory's Link to Jellyfin dialog. A
 * duplicate imported identity, a proof presented for another target, and an injected
 * export write failure leave the account disabled with its exports in place; the
 * retried link moves both personal exports under the new Jellyfin export ID and
 * re-enables the account, which then signs in with Jellyfin and sees its old reviews.
 */
export async function verifyLegacyMemberLink({ run, ui, browser, owner, ownerId, provider }) {
  const { page, context } = browser;
  const { expect } = ui;
  const legacy = run.legacyMembers.member;
  const { Casey: casey, Emery: emery } = provider.users;
  assert.deepEqual(accountState(run, legacy.id), { status: "disabled", disable_cause: "unlinked", permission_revision: 2 }, "0030 disabled the local member and bumped its permission revision");
  assert.equal(identityOf(run, legacy.id), null);
  assert.deepEqual(query(run, "SELECT username, display_name FROM users LEFT JOIN user_profiles ON user_id = id WHERE id = ?", legacy.id), [{ username: null, display_name: legacy.name }], "0030 released the local username into the display name");
  const before = exportsOf(run, legacy.id);
  const [{ local_export_id: localExportId }] = query(run, "SELECT local_export_id FROM users WHERE id = ?", legacy.id);
  assert.deepEqual(before.map(row => [row.format, row.target_export_id, Boolean(row.content_bytes)]), [["edl", localExportId, true], ["mcf", localExportId, true]], "the disabled member's personal EDL and MCF live under its local export ID");
  for (const row of before) assert.deepEqual(fs.readFileSync(row.destination_path), Buffer.from(row.content_bytes));
  ui.record("0030 left legacy-member disabled with cause unlinked and no link, its username released into its display name, its personal EDL and MCF still under its local export ID");

  const open = page.getByRole("dialog", { name: "Account details", exact: true });
  if (await open.count()) await open.getByRole("button", { name: "Close", exact: true }).click();
  await page.goto(`${ui.origin}/#/settings/users`);
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name: `View ${legacy.name}`, exact: true }) });
  await expect(row).toContainText("Disabled: no Jellyfin link");
  await expect(row.getByRole("button", { name: `Link ${legacy.name} to Jellyfin`, exact: true })).toBeVisible();
  assert.equal(await row.getByRole("button", { name: /^Enable/ }).count(), 0, "no enable control for an unlinked account");
  await ui.evidence(page, "legacy-member-row-1440");
  await page.setViewportSize({ width: 320, height: 900 });
  await ui.evidence(page, "legacy-member-row-320");
  await page.setViewportSize({ width: 1440, height: 900 });
  await row.getByRole("button", { name: `View ${legacy.name}`, exact: true }).click();
  const details = page.getByRole("dialog", { name: "Account details", exact: true });
  await expect(details.getByRole("region", { name: "Jellyfin link", exact: true })).toContainText("Not linked to Jellyfin. The account cannot sign in until it is linked.");
  await details.getByRole("button", { name: "Close", exact: true }).click();
  ui.record("Users marks legacy-member Disabled: no Jellyfin link with a Link to Jellyfin action and no enable control at 1440 and 320");

  await row.getByRole("button", { name: `Link ${legacy.name} to Jellyfin`, exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Link to Jellyfin", exact: true });
  assert.equal(await dialog.getByLabel(/account's (own )?password/i).count(), 0, "the dialog no longer asks for the account's own password");
  const proveAs = (username, password) => ui.prove(page, dialog, { username, password, actorPassword: owner.password });
  const duplicate = await proveAs("Emery", emery.password);
  assert.deepEqual([duplicate.status, duplicate.body.preview.blockers], [200, ["jellyfin_identity_in_use"]]);
  await expect(dialog.getByRole("region", { name: "Link blockers", exact: true })).toContainText("Editarr never merges accounts. Delete one of the accounts or transfer its data explicitly, then link again.");
  assert.equal(await dialog.getByRole("button", { name: "Confirm link", exact: true }).count(), 0);
  await ui.noUnlinkOrMerge(page);
  await ui.evidence(page, "legacy-duplicate-blocker-1440");
  await page.setViewportSize({ width: 320, height: 900 });
  await ui.evidence(page, "legacy-duplicate-blocker-320");
  await page.setViewportSize({ width: 1440, height: 900 });
  assertUnmoved(run, legacy.id, before, emery.userId);
  ui.record("a duplicate imported identity blocks linking legacy-member with the explicit deletion-or-transfer explanation and no confirm or merge");

  await dialog.getByRole("button", { name: "Start again", exact: true }).click();
  const forged = await proveAs("Casey", casey.password);
  assert.equal(forged.status, 200);
  assert.deepEqual([forged.body.preview.proof_basis, forged.body.preview.blockers, forged.body.preview.status], ["admin_attested", [], { current: "disabled", after: "active", cause: "unlinked" }]);
  const impact = dialog.getByRole("region", { name: "Link impact", exact: true });
  await expect(impact).toContainText("Admin attested.");
  await expect(impact).toContainText("disabled (no Jellyfin link) becomes active.");
  await expect(impact).toContainText(`${localExportId} becomes ${casey.userId}`);
  await ui.evidence(page, "legacy-preview-1440");
  const reauthentication = { password: owner.password };
  const otherTarget = await ui.api(context, "POST", `/api/users/${ownerId}/link`, { reauthentication, proof: forged.body.proof, confirmation: forged.body.confirmation }, 409);
  assert.equal(otherTarget.code, "link_proof_invalid");
  const spent = await ui.confirm(page, dialog, owner.password);
  assert.deepEqual([spent.status, spent.body.code], [409, "link_proof_invalid"]);
  await expect(dialog.getByRole("alert")).toHaveText("That proof is no longer valid. Prove both identities again.");
  assert.equal(identityOf(run, legacy.id), null);
  assertUnmoved(run, legacy.id, before, casey.userId);
  ui.record("the admin-attested preview shows disabled (no Jellyfin link) becomes active and the export ID move; a proof presented for another target is refused and destroyed, and the UI returns to the proof form");

  const proven = await proveAs("Casey", casey.password);
  assert.equal(proven.status, 200);
  const blocked = path.join(path.dirname(path.dirname(before[0].destination_path)), casey.userId);
  fs.mkdirSync(blocked);
  fs.chmodSync(blocked, 0o555);
  let failed;
  try { failed = await ui.confirm(page, dialog, owner.password); }
  finally { if (fs.existsSync(blocked)) fs.chmodSync(blocked, 0o755); }
  assert.deepEqual([failed.status, failed.body.code], [409, "write_failed"]);
  const stopped = dialog.getByRole("region", { name: "Link stopped", exact: true });
  await expect(stopped).toContainText("Nothing changed. The account keeps its identity and its existing exports.");
  await ui.evidence(page, "legacy-write-failed-1440");
  assert.equal(identityOf(run, legacy.id), null);
  assert.deepEqual(accountState(run, legacy.id), { status: "disabled", disable_cause: "unlinked", permission_revision: 2 }, "a failed link leaves the account disabled");
  assertUnmoved(run, legacy.id, before, casey.userId);
  const rolledBack = query(run, "SELECT kind, state, failure_reason FROM account_link_transitions WHERE user_id = ? ORDER BY id", legacy.id);
  assert.deepEqual(rolledBack, [{ kind: "link", state: "rolled_back", failure_reason: "write_failed" }]);
  ui.record("an interrupted export write keeps legacy-member disabled with its old exports and no identity, says nothing changed, and offers to start again");

  await stopped.getByRole("button", { name: "Start again", exact: true }).click();
  const retried = await proveAs("Casey", casey.password);
  assert.equal(retried.status, 200);
  const linked = await ui.confirm(page, dialog, owner.password);
  assert.deepEqual([linked.status, linked.body.state], [200, "completed"]);
  await expect(dialog.getByRole("region", { name: "Link result", exact: true })).toContainText("Linked to Jellyfin user Casey.");
  const files = assertMoved(run, legacy.id, before, casey.userId);
  assert.deepEqual(identityOf(run, legacy.id), { server_id: provider.serverId, provider_user_id: casey.userId, link_state: "verified" });
  const after = accountState(run, legacy.id);
  assert.deepEqual([after.status, after.disable_cause], ["active", null], "linking an unlinked account re-enables it");
  assert.ok(after.permission_revision > 2, "the re-enable bumps the permission revision");
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  ui.record("the retried link moved both formats under the Jellyfin export ID, removed the old files, and re-enabled legacy-member");

  const memberBrowser = await ui.open(1440);
  await ui.signIn(memberBrowser, "Casey", casey.password);
  const profile = memberBrowser.page.getByRole("region", { name: "Jellyfin link", exact: true });
  await expect(profile).toContainText("Linked to Jellyfin.");
  const status = await ui.api(memberBrowser.context, "GET", "/api/auth/status", undefined, 200);
  assert.equal(status.access.principal.user_id, legacy.id, "Casey's Jellyfin sign-in opens the former local account");
  const film = run.legacyMembers.film;
  const listed = await ui.api(memberBrowser.context, "GET", `/api/library-items/${film.id}/reviews?media_source_id=${film.version}`, undefined, 200);
  assert.deepEqual(listed.map(entry => entry.id).sort(), legacy.reviews.map(entry => entry.id).sort(), "the member reads every review it owned before 0030");
  await memberBrowser.page.goto(`${ui.origin}/${itemRoute(film)}`);
  await expect(memberBrowser.page.getByRole("heading", { name: film.title, exact: true })).toBeVisible();
  for (const { name } of legacy.reviews) await expect(memberBrowser.page.getByRole("button", { name: `Open review ${name}`, exact: true })).toBeVisible();
  await ui.evidence(memberBrowser.page, "legacy-member-reviews-1440");
  await memberBrowser.page.setViewportSize({ width: 320, height: 900 });
  await ui.evidence(memberBrowser.page, "legacy-member-reviews-320");
  ui.record("the re-enabled member signs in with Jellyfin as Casey and sees its pre-0030 reviews at 1440 and 320");
  return {
    member: legacy.id, localExportId, files, preview: forged.body.preview.status, rolledBack,
    refusals: { duplicate: duplicate.body.preview.blockers, otherTarget: otherTarget.code, spentProof: spent.body.code, writeFailed: failed.body.code },
    after: { status: after.status, cause: after.disable_cause, identity: identityOf(run, legacy.id) }, reviews: listed.map(entry => entry.id),
  };
}
