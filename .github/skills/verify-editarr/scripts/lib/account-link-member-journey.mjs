import assert from "node:assert/strict";
import { assertUnmoved, exportsOf, identityOf } from "./account-link-support.mjs";

async function memberDetails(ui, page, name) {
  const open = page.getByRole("dialog", { name: "Account details", exact: true });
  if (await open.count()) await open.getByRole("button", { name: "Close", exact: true }).click();
  await page.goto(`${ui.origin}/#/settings/users`);
  await page.getByRole("button", { name: `View ${name}`, exact: true }).click();
  const details = page.getByRole("dialog", { name: "Account details", exact: true });
  await ui.expect(details.getByRole("heading", { name, exact: true })).toBeVisible();
  return { details, section: details.getByRole("region", { name: "Jellyfin link", exact: true }) };
}

async function memberProfile(ui, memberBrowser) {
  await memberBrowser.page.goto(`${ui.origin}/#/settings/profile`);
  await memberBrowser.page.reload();
  return memberBrowser.page.getByRole("region", { name: "Jellyfin link", exact: true });
}

/**
 * JF-06 leaves no local member to link: every active account already has a Jellyfin
 * link, and only migration 0030 makes unlinked accounts. The owner reads the review
 * member, a Jellyfin user with personal EDL and MCF publications: Users offers no link
 * action, a proof against another Jellyfin user is admin-attested and blocked as
 * already linked, the removed account-password field is refused, a forged target and
 * a blocked confirmation are refused, and nothing moves.
 */
export async function verifyMemberLink({ run, ui, browser, call, as, owner, member, provider, ownerId }) {
  const { page, context } = browser;
  const { expect } = ui;
  const memberCall = as(member.actor);
  const before = exportsOf(run, member.id);
  for (const format of ["edl", "mcf"]) assert.ok(before.some(row => row.format === format && row.content_bytes));
  const { Casey: casey, Emery: emery } = provider.users;
  const ownerIdentity = identityOf(run, ownerId);
  const memberIdentity = identityOf(run, member.id);
  assert.equal(memberIdentity?.link_state, "verified", "the review member is linked by its first Jellyfin sign-in");
  const jellyfin = { username: "Casey", password: casey.password };
  const ordinary = await memberCall("POST", `/api/users/${member.id}/link/proof`, { reauthentication: { password: member.password }, jellyfin }, 403);
  const ordinaryOther = await memberCall("POST", `/api/users/${ownerId}/link/proof`, { reauthentication: { password: member.password }, jellyfin }, 404);
  const memberBrowser = await ui.open(1440);
  await ui.signIn(memberBrowser, member.username, member.password);
  let profile = await memberProfile(ui, memberBrowser);
  await expect(profile).toContainText("Linked to Jellyfin.");
  assert.equal(await profile.getByRole("button").count(), 0);
  ui.record("an ordinary linked member sees no link action and the server refuses its own and another account's proof (403, 404)");

  await call("POST", "/api/jellyfin/users/import", { user_ids: [emery.userId] }, 200);
  const { section } = await memberDetails(ui, page, member.username);
  await expect(section).toContainText("Linked to Jellyfin. Name and avatar sync from Jellyfin. A linked account cannot be unlinked.");
  assert.equal(await section.getByRole("button", { name: /Link to Jellyfin|Relink/ }).count(), 0, "Users offers no link action for a linked account");
  await ui.noUnlinkOrMerge(page);
  await ui.evidence(page, "linked-member-section-1440");
  await page.setViewportSize({ width: 320, height: 900 });
  await ui.evidence(page, "linked-member-section-320");
  await page.setViewportSize({ width: 1440, height: 900 });
  ui.record("Users shows the review member as linked and offers no link, relink, unlink, or merge action at 1440 and 320");

  const reauthentication = { password: owner.password };
  const legacy = await call("POST", `/api/users/${member.id}/link/proof`, { reauthentication, account_password: member.password, jellyfin }, 422);
  const duplicate = await call("POST", `/api/users/${member.id}/link/proof`, { reauthentication, jellyfin: { username: "Emery", password: emery.password } }, 200);
  run.secrets.add(duplicate.proof);
  assert.deepEqual(duplicate.preview.blockers.slice().sort(), ["already_linked", "jellyfin_identity_in_use"]);
  const forged = await call("POST", `/api/users/${member.id}/link/proof`, { reauthentication, jellyfin }, 200);
  run.secrets.add(forged.proof);
  assert.deepEqual([forged.preview.proof_basis, forged.preview.blockers, forged.preview.status], ["admin_attested", ["already_linked"], { current: "active", after: "active", cause: null }]);
  const otherTarget = await ui.api(context, "POST", `/api/users/${ownerId}/link`, { reauthentication, proof: forged.proof, confirmation: forged.confirmation }, 409);
  assert.equal(otherTarget.code, "link_proof_invalid");
  const blocked = await call("POST", `/api/users/${member.id}/link/proof`, { reauthentication, jellyfin }, 200);
  run.secrets.add(blocked.proof);
  const confirmed = await ui.api(context, "POST", `/api/users/${member.id}/link`, { reauthentication, proof: blocked.proof, confirmation: blocked.confirmation }, 409);
  assert.deepEqual(identityOf(run, ownerId), ownerIdentity);
  assert.deepEqual(identityOf(run, member.id), memberIdentity);
  assertUnmoved(run, member.id, before, casey.userId);
  ui.record("a proof for a linked member is admin-attested and blocked as already linked, the removed account password is a 422, a forged target and a blocked confirmation are refused 409, and no identity or export moves");
  return { member: member.id, identity: memberIdentity, refusals: { ordinaryOwnProof: ordinary.code, ordinaryOtherProof: ordinaryOther.code, accountPassword: 422, legacyDetail: legacy.detail?.[0]?.type ?? legacy.code ?? null, duplicate: duplicate.preview.blockers, alreadyLinked: forged.preview.blockers, otherTarget: otherTarget.code, blockedConfirmation: confirmed.code ?? confirmed.detail?.code ?? null } };
}
