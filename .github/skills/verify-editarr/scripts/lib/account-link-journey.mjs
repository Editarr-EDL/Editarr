import assert from "node:assert/strict";
import { linkUI } from "./account-link-ui.mjs";
import { verifyLegacyMemberLink } from "./account-link-legacy-journey.mjs";
import { verifyMemberLink } from "./account-link-member-journey.mjs";
import { runJellyfinUsers } from "./account-link-support.mjs";
import { query } from "./database.mjs";

/**
 * Drive MU-39's link surface against the real API with the run's Jellyfin owner: the
 * review member, a Jellyfin user with personal EDL and MCF publications, gets only
 * refusals, and `legacy-member`, the local password account the seeded 0029 database
 * held and 0030 disabled, is linked from the Users dialog; see features/account-linking.md.
 */
export async function verifyAccountLink(run, { api, admin, owner, member }) {
  const ui = linkUI(run);
  const statuses = [];
  const as = actor => async (method, url, data, status) => {
    const body = await api(actor, method, url, data, status);
    statuses.push({ method, path: url, status });
    return body;
  };
  const call = as(admin);
  try {
    const memberCall = as(member.actor);
    const detail = await memberCall("GET", member.reviewUrl, null, 200);
    const token = { revision: detail.revision, policy_owner_id: detail.policy_owner_id, policy_revision: detail.policy_revision };
    for (const format of ["edl", "mcf"]) await memberCall("POST", `${member.reviewUrl}/exports/${format}`, token, 200);
    const provider = await runJellyfinUsers(run, "Casey", "Emery");
    const browser = await ui.open(1440);
    await ui.signIn(browser, owner.username, owner.password);
    const [{ id: ownerId }] = query(run, "SELECT id FROM users WHERE role = 'owner'");
    const memberLink = await verifyMemberLink({ run, ui, browser, call, as, owner, member, provider, ownerId });
    const legacyLink = await verifyLegacyMemberLink({ run, ui, browser, owner, ownerId, provider });
    assert.deepEqual(ui.errors, []);
    run.write("account-link-result.json", {
      passed: true, feature: "MU-35 + MU-39", owner: ownerId, memberLink, legacyLink,
      ui: { steps: ui.steps, screenshots: ui.screenshots, viewports: [1440, 320], browserErrors: ui.errors },
      scope: "Browser-driven MU-39 link section and real link API. The owner and the review member are linked to the run's Jellyfin 12, reached through the run's synthetic catalog, which relays user routes. The run starts from a seeded 0029 database whose local password member real startup disables with cause unlinked; the owner links it to a new Jellyfin user from the Users dialog through a duplicate identity, a forged target, and an injected export write failure, and the member then signs in with Jellyfin. Independent SQLite and file reads.",
    });
    console.log("PASS: the linked review member has no link action and its proofs are blocked; the 0030-disabled local member was linked from Users through duplicate, forged-target, and write-failure refusals, moved its EDL and MCF exports, was re-enabled, and signed in with Jellyfin to its old reviews.");
  } finally {
    await ui.close();
    run.write("account-link-http-statuses.json", statuses);
  }
}
