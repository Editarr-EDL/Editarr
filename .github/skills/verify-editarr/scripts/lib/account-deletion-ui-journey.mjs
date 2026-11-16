import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import { capture } from "./capture.mjs";
import { digest } from "./account-link-support.mjs";
import { query } from "./database.mjs";
import { linkUI } from "./account-link-ui.mjs";
import { MCF } from "./account-deletion-support.mjs";
import { verifyBulkDeletion, verifyDuplicateLink, verifyPermanentDeletion, verifyProtectedTargets, verifyRetention, verifyTransfer, verifyUnconfirmedDeletion } from "./account-deletion-ui-steps.mjs";
import { SIGN_IN_PATH, addJellyfinUser } from "./run-jellyfin.mjs";

/**
 * Drive MU-41's deletion UI against the real API with synthetic members, reviews, and
 * sidecars: protected targets through row, bulk, and deep link; keyboard retention with a
 * stale preview; transfer from a deep link; permanent deletion at 320 px; a partial bulk
 * run including the community member left by `--community`; and MU-39's duplicate-identity
 * blocker on the 0030-disabled legacy-linker resolved by a reviewed retention of the holder
 * before linking again. SQLite and file reads
 * check every outcome.
 */
export async function verifyAccountDeletionUI(run, owner, media, target) {
  const ui = linkUI(run, "deletion-ui");
  const origin = ui.origin;
  const statuses = [];
  const contexts = [];
  const secret = () => { const value = randomBytes(24).toString("base64url"); run.secrets.add(value); return value; };

  async function session(username, password) {
    const context = await run.browser.newContext();
    contexts.push(context);
    const response = await context.request.post(`${origin}${SIGN_IN_PATH}`, { data: { username, password }, headers: { Origin: origin } });
    assert.equal(response.status(), 200, `login ${username}`);
    for (const cookie of await context.cookies()) run.secrets.add(cookie.value);
    return context;
  }

  /** Call the API in a context's own session, with its CSRF token, and check the status. */
  async function call(context, method, pathname, { data, multipart } = {}, expected = 200) {
    const state = await (await context.request.get(`${origin}/api/auth/status`)).json();
    if (state.csrf_token) run.secrets.add(state.csrf_token);
    const response = await context.request.fetch(origin + pathname, { method, ...(multipart ? { multipart } : data ? { data } : {}), headers: { Origin: origin, ...(state.csrf_token ? { "X-CSRF-Token": state.csrf_token } : {}) } });
    const body = response.status() === 204 ? {} : await response.json().catch(() => null);
    for (const confirmation of [body?.confirmation, body?.detail?.confirmation]) if (confirmation) run.secrets.add(confirmation);
    statuses.push({ method, path: pathname, status: response.status(), code: body?.detail?.code ?? body?.code ?? null });
    assert.equal(response.status(), expected, `${method} ${pathname}: ${JSON.stringify(body)}`);
    return body;
  }

  const browser = await ui.open(1440);
  try {
    await ui.signIn(browser, owner.username, owner.password, "users");
    const fresh = { reauthentication: { password: owner.password } };
    async function member(username, role = "user") {
      const { password } = await addJellyfinUser(run, username);
      const context = await session(username, password);
      const { user_id: id } = (await call(context, "GET", "/api/auth/status")).access.principal;
      if (role === "admin") await call(browser.context, "PUT", `/api/users/${id}/role`, { data: { role: "admin", ...fresh } });
      return { id, username, password, context };
    }
    const importReview = async (who, name) => (await call(who.context, "POST", "/api/mcf-imports/upload", { multipart: {
      options: JSON.stringify({ target, name, synchronization: { start_seconds: 0, end_seconds: media.durationSeconds, end_mode: "file_end" }, mcf_material_end_seconds: 4 }),
      file: { name: `${name}.mcf`, mimeType: "text/vtt", buffer: Buffer.from(MCF) },
    } }, 201)).review;
    async function publish(who, name) {
      const review = await importReview(who, name);
      const ids = {};
      for (const format of ["edl", "mcf"]) {
        const data = { revision: review.revision, policy_owner_id: review.policy_owner_id, policy_revision: review.policy_revision, target: "self" };
        const state = await (await who.context.request.get(`${origin}/api/auth/status`)).json();
        const first = await who.context.request.post(`${origin}/api/reviews/${review.id}/exports/${format}`, { data, headers: { Origin: origin, "X-CSRF-Token": state.csrf_token } });
        const body = await first.json();
        const published = first.status() === 409 && body.detail?.code === "export_confirmation_required"
          ? await call(who.context, "POST", `/api/reviews/${review.id}/exports/${format}`, { data: { ...data, confirmation: body.detail.confirmation } })
          : (assert.equal(first.status(), 200, JSON.stringify(body)), body);
        ids[format] = published.id;
      }
      return { review, ids };
    }
    const files = ids => Object.fromEntries(ids.map(id => {
      const [row] = query(run, "SELECT review_id, target_user_id, target_export_id, destination_path, content_bytes FROM media_item_exports WHERE id = ?", id);
      if (!row) return [id, null];
      return [id, { review_id: row.review_id, target_user_id: row.target_user_id, target_export_id: row.target_export_id, path: row.destination_path,
        contentSha256: row.content_bytes ? digest(row.content_bytes) : null, diskSha256: fs.existsSync(row.destination_path) ? digest(fs.readFileSync(row.destination_path)) : null }];
    }));
    const account = id => {
      const [row] = query(run, "SELECT status, EXISTS (SELECT 1 FROM user_provider_identities WHERE user_id = users.id) AS linked, username FROM users WHERE id = ?", id);
      const [disposition] = query(run, "SELECT mode, destination_id, actor_id FROM account_dispositions WHERE user_id = ?", id);
      return { status: row.status, linked: Boolean(row.linked), username: row.username, disposition: disposition ? { ...disposition } : null };
    };
    const [{ id: ownerId }] = query(run, "SELECT id FROM users WHERE role = 'owner'");
    const context = { run, ui, browser, owner, ownerId, origin, call, session, member, publish, importReview, files, account, fresh };

    const protectedTargets = await verifyProtectedTargets(context);
    const retention = await verifyRetention(context);
    const transfer = await verifyTransfer(context);
    const permanent = await verifyPermanentDeletion(context);
    const unconfirmed = await verifyUnconfirmedDeletion(context);
    const bulk = await verifyBulkDeletion(context);
    const duplicate = await verifyDuplicateLink(context);
    assert.deepEqual(ui.errors, []);
    assert.equal(digest(fs.readFileSync(media.file)), media.sha256);
    run.write("account-deletion-ui-result.json", {
      passed: true, feature: "MU-41", protectedTargets, retention, transfer, permanent, unconfirmed, bulk, duplicate,
      ui: { steps: ui.steps, screenshots: ui.screenshots, viewports: [1440, 320], browserErrors: ui.errors }, mediaUnchanged: true,
      scope: "Browser-driven MU-41 deletion UI against the real API on the isolated source app with Jellyfin members, MCF reviews, EDL and MCF sidecars, the --community journey's community member, and legacy-linker, a local password account from the seeded 0029 database that startup's 0030 disabled with cause unlinked, linked from its dialog past a new Jellyfin user that already held the identity. Independent SQLite and file reads after each step. No live Jellyfin or community service.",
    });
    console.log("PASS: deletion UI refused protected targets by row, bulk, and deep link; retained, transferred, and permanently deleted through reviewed previews with a stale refusal and keyboard operation; reported partial bulk results per target; and resolved a duplicate identity on the 0030-disabled legacy-linker by a reviewed retention of the holder before linking again.");
  } catch (error) {
    await capture(browser.page, run, "deletion-ui-failure").catch(() => {});
    throw error;
  } finally {
    run.write("account-deletion-ui-api-statuses.json", statuses);
    for (const context of contexts) {
      for (const cookie of await context.cookies().catch(() => [])) run.secrets.add(cookie.value);
      await context.close().catch(() => {});
    }
    await ui.close();
  }
}
