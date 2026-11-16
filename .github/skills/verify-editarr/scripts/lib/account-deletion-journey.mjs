import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { query } from "./database.mjs";
import { MCF } from "./account-deletion-support.mjs";
import { verifyAccountDeletionUI } from "./account-deletion-ui-journey.mjs";
import { SIGN_IN_PATH, addJellyfinUser } from "./run-jellyfin.mjs";

const digest = content => createHash("sha256").update(content).digest("hex");

function exportsOf(run, ids) {
  return Object.fromEntries(ids.map(id => {
    const [row] = query(run, "SELECT id, review_id, policy_owner_id, original_author_id, destination_path, content_bytes FROM media_item_exports WHERE id = ?", id);
    if (!row) return [id, null];
    return [id, {
      review_id: row.review_id, policy_owner_id: row.policy_owner_id, original_author_id: row.original_author_id,
      path: row.destination_path,
      contentSha256: row.content_bytes ? digest(row.content_bytes) : null,
      diskSha256: fs.existsSync(row.destination_path) ? digest(fs.readFileSync(row.destination_path)) : null,
    }];
  }));
}

function account(run, id) {
  const [row] = query(run, "SELECT status, disable_cause, EXISTS (SELECT 1 FROM user_provider_identities WHERE user_id = users.id) AS linked, username FROM users WHERE id = ?", id);
  const [stored] = query(run, "SELECT mode, actor_id, confirmation, result_json FROM account_dispositions WHERE user_id = ?", id);
  const keys = query(run, "SELECT count(*) AS count FROM api_keys WHERE user_id = ?", id)[0].count;
  let disposition = null;
  if (stored) {
    const { confirmation, ...kept } = stored;
    run.secrets.add(confirmation);
    disposition = { ...kept, confirmationSha256: digest(confirmation) };
  }
  return { ...row, linked: Boolean(row.linked), keys, disposition };
}

export async function verifyAccountDeletion(run, owner, media, target) {
  const { request } = createRequire(path.join(run.root, "web/package.json"))("@playwright/test");
  const origin = run.manifest.origin;
  const contexts = [];
  const requests = [];
  const secret = () => { const value = randomBytes(24).toString("base64url"); run.secrets.add(value); return value; };
  async function actor(headers = {}) {
    const context = await request.newContext({ baseURL: origin, extraHTTPHeaders: { Origin: origin, ...headers } });
    contexts.push(context);
    let csrf;
    return {
      async send(method, url, data, multipart) {
        const response = await context.fetch(url, { method, headers: csrf ? { "X-CSRF-Token": csrf } : {}, ...(multipart ? { multipart } : data ? { data } : {}) });
        const body = response.status() === 204 ? {} : await response.json();
        if (body.csrf_token) { csrf = body.csrf_token; run.secrets.add(csrf); }
        for (const confirmation of [body.confirmation, body.detail?.confirmation]) if (confirmation) run.secrets.add(confirmation);
        for (const cookie of (await context.storageState()).cookies) run.secrets.add(cookie.value);
        return { status: response.status(), body };
      },
    };
  }
  const api = async (who, method, url, data, status = 200, multipart) => {
    const result = await who.send(method, url, data, multipart);
    requests.push({ method, path: url, status: result.status, code: result.body?.detail?.code ?? result.body?.code ?? null });
    assert.equal(result.status, status, `${method} ${url}: ${JSON.stringify(result.body)}`);
    return result.body;
  };
  const fresh = { reauthentication: { password: owner.password } };
  const communityEvents = () => query(run, "SELECT count(*) AS count FROM audit_events WHERE action LIKE 'community.%'")[0].count;
  const communityBefore = communityEvents();
  const deletion = (who, id, mode, preview, files = [], status = 200, extra = {}) => api(who, "POST", `/api/users/${id}/deletion`, { mode, confirmation: preview.confirmation, files, ...fresh, ...extra }, status);
  try {
    const admin = await actor();
    await api(admin, "POST", SIGN_IN_PATH, { username: owner.username, password: owner.password });
    const ownerId = (await api(admin, "GET", "/api/auth/status")).access.principal.user_id;
    const members = {};
    for (const name of ["retained-member", "deleted-member", "transferred-member", "transfer-destination", "bulk-member", "bulk-other", "deleting-admin"]) {
      const { password } = await addJellyfinUser(run, name);
      const session = await actor();
      const signedIn = await api(session, "POST", SIGN_IN_PATH, { username: name, password });
      members[name] = { id: signedIn.access.principal.user_id, session, password };
    }
    await api(admin, "PUT", `/api/users/${members["deleting-admin"].id}/role`, { role: "admin", ...fresh });
    const importOwn = async (who, name) => (await api(who, "POST", "/api/mcf-imports/upload", null, 201, {
      options: JSON.stringify({ target, name, synchronization: { start_seconds: 0, end_seconds: media.durationSeconds, end_mode: "file_end" }, mcf_material_end_seconds: 4 }),
      file: { name: `${name}.mcf`, mimeType: "text/vtt", buffer: Buffer.from(MCF) },
    })).review;
    const publishOwn = async (who, name) => {
      const review = await importOwn(who, name);
      const ids = {};
      for (const format of ["edl", "mcf"]) {
        const body = { revision: review.revision, policy_owner_id: review.policy_owner_id, policy_revision: review.policy_revision, target: "self" };
        const first = await who.send("POST", `/api/reviews/${review.id}/exports/${format}`, body);
        requests.push({ method: "POST", path: `/api/reviews/${review.id}/exports/${format}`, status: first.status, code: first.body?.detail?.code ?? null });
        const published = first.status === 409 && first.body.detail?.code === "export_confirmation_required"
          ? await api(who, "POST", `/api/reviews/${review.id}/exports/${format}`, { ...body, confirmation: first.body.detail.confirmation })
          : (assert.equal(first.status, 200, JSON.stringify(first.body)), first.body);
        ids[format] = published.id;
      }
      return { review, ids };
    };

    const deletingAdmin = members["deleting-admin"].session;
    const protectedOwner = await api(deletingAdmin, "GET", `/api/users/${ownerId}/deletion?mode=retain`, null, 403);
    const protectedPreview = await api(admin, "GET", `/api/users/${members["bulk-member"].id}/deletion?mode=retain`);
    const substituted = await api(deletingAdmin, "POST", "/api/users/bulk-deletion", { targets: [{ user_id: ownerId, mode: "retain", confirmation: protectedPreview.confirmation }], reauthentication: { password: members["deleting-admin"].password } });
    assert.deepEqual(substituted.results.map(item => [item.outcome, item.code]), [["refused", "protected_account"]]);
    await api(admin, "GET", `/api/users/${ownerId}/deletion?mode=retain`, null, 409);
    assert.equal(account(run, ownerId).status, "active");

    const retained = members["retained-member"];
    const retainedKey = (await api(retained.session, "POST", "/api/auth/api-key", { reauthentication: { password: retained.password } })).api_key;
    run.secrets.add(retainedKey);
    const keyActor = await actor({ "X-API-Key": retainedKey });
    await api(keyActor, "GET", `/api/users/${retained.id}`);
    const kept = await publishOwn(retained.session, "Retained account review");
    const keptIds = [kept.ids.edl, kept.ids.mcf];
    const keptBefore = exportsOf(run, keptIds);
    for (const row of Object.values(keptBefore)) assert.equal(row.diskSha256, row.contentSha256);
    const stalePreview = await api(admin, "GET", `/api/users/${retained.id}/deletion?mode=retain`);
    const lateReview = await importOwn(retained.session, "Created after preview");
    const stale = await deletion(admin, retained.id, "retain", stalePreview, [], 409);
    assert.equal(stale.code, "stale_account_deletion");
    assert.equal(account(run, retained.id).status, "active");
    const retainPreview = await api(admin, "GET", `/api/users/${retained.id}/deletion?mode=retain`);
    assert.equal(retainPreview.reviews.count, 2);
    const ownerKey = (await api(admin, "POST", "/api/auth/api-key", fresh)).api_key;
    run.secrets.add(ownerKey);
    const ownerKeyActor = await actor({ "X-API-Key": ownerKey });
    const keyRefusal = await api(ownerKeyActor, "POST", `/api/users/${retained.id}/deletion`, { mode: "retain", confirmation: retainPreview.confirmation, ...fresh }, 403);
    assert.equal(keyRefusal.code ?? keyRefusal.detail?.code, "interactive_session_required");
    const unproven = await api(admin, "POST", `/api/users/${retained.id}/deletion`, { mode: "retain", confirmation: retainPreview.confirmation }, 403);
    assert.equal(unproven.code ?? unproven.detail?.code, "reauthentication_required");
    assert.equal(account(run, retained.id).status, "active");
    assert.equal(retainPreview.credentials.api_key, true);
    const retainResult = await deletion(admin, retained.id, "retain", retainPreview);
    assert.equal(retainResult.replayed, false);
    assert.equal(retainResult.api_key_revoked, true);
    assert.deepEqual(retainResult.reviews_retained.sort(), [kept.review.id, lateReview.id].sort());
    const retainReplay = await deletion(admin, retained.id, "retain", retainPreview);
    assert.deepEqual(retainReplay, { ...retainResult, replayed: true });
    await api(retained.session, "GET", `/api/users/${retained.id}`, null, 401);
    await api(keyActor, "GET", `/api/users/${retained.id}`, null, 401);
    const relogin = await actor();
    const refusedLogin = await relogin.send("POST", SIGN_IN_PATH, { username: "retained-member", password: retained.password });
    requests.push({ method: "POST", path: SIGN_IN_PATH, status: refusedLogin.status, code: refusedLogin.body?.code ?? null });
    if (refusedLogin.status === 200) assert.notEqual(refusedLogin.body.access.principal.user_id, retained.id, "the released Jellyfin user never signs back into the retained account");
    else assert.ok([401, 403].includes(refusedLogin.status));
    await api(admin, "GET", `/api/users/${retained.id}`, null, 404);
    const keptAfter = exportsOf(run, keptIds);
    assert.deepEqual(keptAfter, keptBefore);
    const keptAccount = account(run, retained.id);
    assert.deepEqual({ ...keptAccount, disposition: undefined }, { status: "disabled", disable_cause: "local", linked: false, username: null, keys: 0, disposition: undefined });
    assert.equal(keptAccount.disposition.mode, "retain");
    const { replayed: _replayed, ...storedResult } = retainResult;
    assert.deepEqual(JSON.parse(keptAccount.disposition.result_json).result, storedResult);
    assert.equal(query(run, "SELECT count(*) AS count FROM saved_reviews WHERE owner_id = ?", retained.id)[0].count, 2);

    const removed = members["deleted-member"];
    const doomed = await publishOwn(removed.session, "Deleted account review");
    const doomedReview = await api(admin, "GET", `/api/reviews/${doomed.review.id}`);
    const copy = await api(admin, "POST", `/api/reviews/${doomed.review.id}/duplicate`, { revision: doomedReview.revision, name: "Owner copy keeps evidence", policy_owner_id: doomedReview.policy_owner_id, policy_revision: doomedReview.policy_revision }, 201);
    const sharedScan = query(run, "SELECT scan_id FROM review_scan_memberships WHERE review_id = ?", doomed.review.id)[0].scan_id;
    const doomedBefore = exportsOf(run, [doomed.ids.edl, doomed.ids.mcf]);
    const deletePreview = await api(admin, "GET", `/api/users/${removed.id}/deletion?mode=delete`);
    assert.deepEqual(deletePreview.blockers, []);
    assert.deepEqual(deletePreview.files.map(file => [file.publication.id, file.choices]).sort(), [[doomed.ids.edl, ["remove", "retain"]], [doomed.ids.mcf, ["remove", "retain"]]].sort());
    assert.deepEqual(deletePreview.scans.items.map(scan => [scan.scan_id, scan.required]), [[sharedScan, true]]);
    const choices = [{ export_id: doomed.ids.edl, choice: "retain" }, { export_id: doomed.ids.mcf, choice: "remove" }];
    const missingChoice = await deletion(admin, removed.id, "delete", deletePreview, choices.slice(0, 1), 422);
    assert.equal(missingChoice.code, "account_deletion_choice_required");
    assert.deepEqual(exportsOf(run, [doomed.ids.edl, doomed.ids.mcf]), doomedBefore);
    const deleteResult = await deletion(admin, removed.id, "delete", deletePreview, choices);
    assert.deepEqual([deleteResult.reviews_deleted, deleteResult.files_retained, deleteResult.files_removed, deleteResult.scans_kept, deleteResult.scans_deleted], [[doomed.review.id], [doomed.ids.edl], [doomed.ids.mcf], [sharedScan], []]);
    const doomedAfter = exportsOf(run, [doomed.ids.edl, doomed.ids.mcf]);
    assert.equal(doomedAfter[doomed.ids.mcf], null);
    assert.equal(fs.existsSync(doomedBefore[doomed.ids.mcf].path), false);
    assert.deepEqual(doomedAfter[doomed.ids.edl], { ...doomedBefore[doomed.ids.edl], review_id: null });
    await api(admin, "GET", `/api/reviews/${doomed.review.id}`, null, 404);
    await api(admin, "GET", `/api/reviews/${copy.id}`);
    assert.equal(query(run, "SELECT count(*) AS count FROM scans WHERE id = ?", sharedScan)[0].count, 1);
    assert.equal(query(run, "SELECT count(*) AS count FROM user_profiles WHERE user_id = ?", removed.id)[0].count, 0);

    const moving = members["transferred-member"];
    const receiver = members["transfer-destination"];
    const moved = await publishOwn(moving.session, "Transferred account review");
    const movedIds = [moved.ids.edl, moved.ids.mcf];
    const movedBefore = exportsOf(run, movedIds);
    const [receiverExportId] = query(run, "SELECT COALESCE((SELECT provider_user_id FROM user_provider_identities WHERE user_id = users.id), local_export_id) AS local_export_id FROM users WHERE id = ?", receiver.id).map(row => row.local_export_id);
    const ownTransfer = await api(admin, "GET", `/api/users/${moving.id}/deletion?mode=transfer&destination=${moving.id}`, null, 409);
    assert.equal(ownTransfer.code ?? ownTransfer.detail?.code, "transfer_destination_unavailable");
    const transferPreview = await api(admin, "GET", `/api/users/${moving.id}/deletion?mode=transfer&destination=${receiver.id}`);
    assert.deepEqual([transferPreview.blockers, transferPreview.transfer, transferPreview.reviews.ids], [[], { files_moved: 2, policy_files: 0 }, [moved.review.id]]);
    const transferResult = await deletion(admin, moving.id, "transfer", transferPreview, [], 200, { destination_id: receiver.id });
    assert.deepEqual([transferResult.reviews_transferred, transferResult.files_moved, transferResult.destination_id, transferResult.personal_files_kept], [[moved.review.id], 2, receiver.id, 0]);
    const movedRows = Object.fromEntries(movedIds.map(id => [id, query(run, "SELECT target_user_id, target_export_id, destination_path FROM media_item_exports WHERE id = ?", id)[0]]));
    const movedFiles = {};
    for (const id of movedIds) {
      const row = movedRows[id];
      assert.deepEqual([row.target_user_id, row.target_export_id], [receiver.id, receiverExportId]);
      assert.notEqual(row.destination_path, movedBefore[id].path);
      assert.ok(row.destination_path.includes(receiverExportId));
      assert.equal(fs.existsSync(movedBefore[id].path), false);
      movedFiles[id] = { from: movedBefore[id].path, to: row.destination_path, beforeSha256: movedBefore[id].diskSha256, afterSha256: digest(fs.readFileSync(row.destination_path)) };
      assert.equal(movedFiles[id].afterSha256, movedFiles[id].beforeSha256);
    }
    const [movedReview] = query(run, "SELECT owner_id, original_author_id FROM saved_reviews WHERE id = ?", moved.review.id);
    assert.deepEqual([movedReview.owner_id, movedReview.original_author_id], [receiver.id, moving.id]);
    const movedAccount = account(run, moving.id);
    assert.deepEqual([movedAccount.status, movedAccount.disposition.mode], ["disabled", "transfer"]);
    assert.equal(query(run, "SELECT destination_id FROM account_dispositions WHERE user_id = ?", moving.id)[0].destination_id, receiver.id);
    assert.deepEqual(query(run, "SELECT count(*) AS count FROM account_transfer_moves WHERE user_id = ? AND outcome = 'removed'", moving.id)[0].count, 2);

    const bulkTarget = members["bulk-member"];
    const bulkOther = members["bulk-other"];
    const bulkPreview = await api(admin, "GET", `/api/users/${bulkTarget.id}/deletion?mode=retain`);
    const bulk = await api(admin, "POST", "/api/users/bulk-deletion", { ...fresh, targets: [
      { user_id: bulkTarget.id, mode: "retain", confirmation: bulkPreview.confirmation },
      { user_id: bulkOther.id, mode: "retain", confirmation: bulkPreview.confirmation },
      { user_id: ownerId, mode: "retain", confirmation: bulkPreview.confirmation },
    ] });
    assert.deepEqual(bulk.results.map(item => [item.user_id, item.outcome, item.code]), [[bulkTarget.id, "applied", null], [bulkOther.id, "refused", "stale_account_deletion"], [ownerId, "refused", "own_account"]]);
    assert.equal(account(run, bulkOther.id).status, "active");
    assert.equal(account(run, bulkTarget.id).status, "disabled");

    const listed = await api(admin, "GET", "/api/users");
    const listedIds = listed.items.map(item => item.id);
    for (const id of [retained.id, removed.id, moving.id, bulkTarget.id]) assert.ok(!listedIds.includes(id));
    assert.ok(listedIds.includes(bulkOther.id) && listedIds.includes(ownerId));
    const audits = query(run, "SELECT outcome, actor_id, target_user_id, details_json FROM audit_events WHERE action = 'account.delete' ORDER BY id");
    assert.deepEqual(audits.filter(row => row.outcome === "succeeded").map(row => row.target_user_id), [retained.id, removed.id, moving.id, bulkTarget.id]);
    assert.deepEqual(audits.filter(row => row.outcome !== "succeeded").map(row => JSON.parse(row.details_json).reason), ["protected_account", "stale_account_deletion", "reauthentication_required", "account_deletion_choice_required", "stale_account_deletion"]);
    const community = communityEvents() - communityBefore;
    assert.equal(community, 0, "deletion records no community events");
    assert.deepEqual(query(run, "PRAGMA foreign_key_check"), []);
    assert.equal(digest(fs.readFileSync(media.file)), media.sha256);
    run.write("account-deletion-result.json", {
      passed: true, feature: "MU-38", members: Object.fromEntries(Object.entries(members).map(([name, value]) => [name, value.id])),
      protectedOwner: protectedOwner.code ?? protectedOwner.detail?.code ?? null, substituted: substituted.results,
      freshAuthentication: { apiKey: keyRefusal.code ?? keyRefusal.detail?.code, withoutPassword: unproven.code ?? unproven.detail?.code },
      retain: { stale: stale.code, result: retainResult, replay: retainReplay.replayed, before: keptBefore, after: keptAfter, account: { ...keptAccount, disposition: { ...keptAccount.disposition, result_json: JSON.parse(keptAccount.disposition.result_json) } } },
      transfer: { destination: receiver.id, destinationExportId: receiverExportId, preview: transferPreview.transfer, result: transferResult, files: movedFiles, review: movedReview, ownDestination: ownTransfer.code ?? ownTransfer.detail?.code },
      delete: { preview: { scans: deletePreview.scans, files: deletePreview.files.map(file => [file.publication.id, file.choices]) }, result: deleteResult, before: doomedBefore, after: doomedAfter, ownerCopy: copy.id },
      bulk: bulk.results.map(item => [item.user_id, item.outcome, item.code]), audits, communityEvents: community, mediaUnchanged: true,
      scope: "Real authenticated source app API with seven Jellyfin members: protected owner refused alone and by bulk substitution, an API key and a request without the password refused, stale retention refused, retention keeps both personal files byte for byte while the session, API key, and password stop working, replay returns the stored result, permanent deletion applies per-file choices and keeps the scan an owner copy needs, transfer moves the review and both personal files to the destination's export ID with identical bytes and removes the old files, and bulk reports applied and refused targets. The community service is not configured in this journey; remote-call absence is proven by the pytest transport spy. Hard-exit recovery is proven by pytest subprocess exits.",
    });
    await verifyAccountDeletionUI(run, owner, media, target);
  } finally {
    run.write("account-deletion-http-statuses.json", requests);
    for (const context of contexts) await context.dispose();
  }
}
