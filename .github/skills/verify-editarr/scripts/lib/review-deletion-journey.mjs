import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { query } from "./database.mjs";
import { regenerationStatus } from "./export-regeneration-journey.mjs";
import { SIGN_IN_PATH, addJellyfinUser } from "./run-jellyfin.mjs";

const digest = content => createHash("sha256").update(content).digest("hex");
const MCF = "WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nTITLE Deletion proof\nTYPE movie\n\nNOTE\nSTART 00:00:00.000\nEND 00:00:04.000\n\n00:00:01.000 --> 00:00:02.000\nviolence=low=video\n";

function inventory(run) {
  const rows = query(run, "SELECT id, review_id, scope, format, target_user_id, actor_id, policy_owner_id, original_author_id, policy_revision, destination_path, content_bytes FROM media_item_exports WHERE scope != 'legacy' ORDER BY id");
  return Object.fromEntries(rows.map(row => [row.id, {
    ...row,
    content_bytes: undefined,
    contentSha256: row.content_bytes ? digest(row.content_bytes) : null,
    diskSha256: fs.existsSync(row.destination_path) ? digest(fs.readFileSync(row.destination_path)) : null,
  }]));
}

const audit = (run, actorId) => query(run, "SELECT action, actor_id, target_user_id, resource FROM audit_events WHERE action IN ('export.delete', 'export.retain') AND actor_id = ? ORDER BY id", actorId);

export async function verifyReviewDeletion(run, owner, media, target) {
  const { request, expect } = createRequire(path.join(run.root, "web/package.json"))("@playwright/test");
  const origin = run.manifest.origin;
  const contexts = [];
  const requests = [];
  const secret = () => { const value = randomBytes(24).toString("base64url"); run.secrets.add(value); return value; };
  async function actor() {
    const context = await request.newContext({ baseURL: origin, extraHTTPHeaders: { Origin: origin } });
    contexts.push(context);
    let csrf;
    return {
      async send(method, url, data, multipart) {
        const response = await context.fetch(url, { method, headers: csrf ? { "X-CSRF-Token": csrf } : {}, ...(multipart ? { multipart } : data ? { data } : {}) });
        const body = response.status() === 204 ? {} : await response.json();
        if (body.csrf_token) { csrf = body.csrf_token; run.secrets.add(csrf); }
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
  try {
    const admin = await actor();
    await api(admin, "POST", SIGN_IN_PATH, { username: owner.username, password: owner.password });
    const { password: chosen } = await addJellyfinUser(run, "deletion-member");
    const member = await actor();
    const created = { id: (await api(member, "POST", SIGN_IN_PATH, { username: "deletion-member", password: chosen })).access.principal.user_id };
    const imported = await api(member, "POST", "/api/mcf-imports/upload", null, 201, {
      options: JSON.stringify({ target, name: "Deletion proof", synchronization: { start_seconds: 0, end_seconds: media.durationSeconds, end_mode: "file_end" }, mcf_material_end_seconds: 4 }),
      file: { name: "deletion.mcf", mimeType: "text/vtt", buffer: Buffer.from(MCF) },
    });
    const review = imported.review;
    const url = `/api/reviews/${review.id}`;
    const publish = async (who, format, targetChoice) => {
      const request = { revision: review.revision, policy_owner_id: review.policy_owner_id, policy_revision: review.policy_revision, target: targetChoice };
      const first = await who.send("POST", `${url}/exports/${format}`, request);
      requests.push({ method: "POST", path: `${url}/exports/${format}`, status: first.status, code: first.body?.detail?.code ?? null });
      if (first.status !== 409 || first.body.detail?.code !== "export_confirmation_required") {
        assert.equal(first.status, 200, JSON.stringify(first.body));
        return first.body;
      }
      return api(who, "POST", `${url}/exports/${format}`, { ...request, confirmation: first.body.detail.confirmation });
    };
    const ids = {
      memberEdl: (await publish(member, "edl", "self")).id,
      memberMcf: (await publish(member, "mcf", "self")).id,
      ownerEdl: (await publish(admin, "edl", "self")).id,
      defaultMcf: (await publish(admin, "mcf", "item_default")).id,
    };
    for (const row of Object.values(inventory(run))) assert.equal(row.diskSha256, row.contentSha256);
    const stalePreview = await api(member, "GET", `${url}/deletion`);
    assert.deepEqual(stalePreview.files.map(file => [file.publication.id, file.choices]).sort(), [[ids.memberEdl, ["remove", "retain"]], [ids.memberMcf, ["remove", "retain"]]].sort());
    const memberText = JSON.stringify(stalePreview);
    for (const hidden of [ids.ownerEdl, ids.defaultMcf]) assert.ok(!memberText.includes(`"id":${hidden},`), "hidden publication is undisclosed");
    const ownerPreview = await api(admin, "GET", `${url}/deletion`);
    assert.deepEqual(ownerPreview.files.map(file => file.publication.id).sort((a, b) => a - b), Object.values(ids).sort((a, b) => a - b));
    await api(admin, "GET", `${url}/deletion`.replace(review.id, "absent"), null, 404);
    const republished = await publish(member, "edl", "self");
    assert.equal(republished.id, ids.memberEdl);
    assert.ok(republished.publication_revision > stalePreview.files.find(file => file.publication.id === ids.memberEdl).publication.publication_revision);
    const before = inventory(run);
    const choices = { [ids.memberEdl]: "retain", [ids.memberMcf]: "remove" };
    const body = preview => ({ revision: preview.revision, confirmation: preview.confirmation, files: Object.entries(choices).map(([id, choice]) => ({ export_id: Number(id), choice })) });
    const stale = await api(member, "POST", `${url}/deletion`, body(stalePreview), 409);
    assert.equal(stale.detail.code, "stale_review_deletion");
    assert.deepEqual(inventory(run), before);
    const current = await api(member, "GET", `${url}/deletion`);
    assert.notEqual(current.confirmation, stalePreview.confirmation);
    const smuggled = await api(member, "POST", `${url}/deletion`, { ...body(current), files: [...body(current).files, { export_id: ids.defaultMcf, choice: "remove" }] }, 422);
    assert.equal(smuggled.detail.code, "review_deletion_choice_required");
    assert.deepEqual(inventory(run), before);
    const result = await api(member, "POST", `${url}/deletion`, body(current));
    assert.deepEqual(result, { review_id: review.id, retained: [ids.memberEdl], removed: [ids.memberMcf] });
    await api(member, "GET", url, null, 404);
    await api(admin, "GET", url, null, 404);
    const after = inventory(run);
    assert.equal(after[ids.memberMcf], undefined);
    assert.equal(fs.existsSync(before[ids.memberMcf].destination_path), false);
    for (const key of ["memberEdl", "ownerEdl", "defaultMcf"]) {
      const old = before[ids[key]];
      const now = after[ids[key]];
      assert.equal(now.review_id, null);
      assert.equal(now.policy_owner_id, created.id);
      assert.equal(now.original_author_id, created.id);
      assert.equal(now.actor_id, old.actor_id);
      assert.equal(now.diskSha256, old.diskSha256);
      assert.equal(now.contentSha256, old.contentSha256);
    }
    const events = audit(run, created.id);
    assert.deepEqual(events.map(event => [event.action, event.resource]).sort(), [["export.delete", `export:${ids.memberMcf}`], ...["memberEdl", "ownerEdl", "defaultMcf"].map(key => ["export.retain", `export:${ids[key]}`])].sort());
    assert.ok(events.every(event => event.actor_id === created.id));
    const retainedStatus = await api(member, "GET", `/api/exports/${ids.memberEdl}`);
    assert.equal(retainedStatus.review_id, null);
    assert.equal(retainedStatus.serving, true);
    await api(member, "GET", `/api/exports/${ids.ownerEdl}`, null, 404);
    const listed = await api(admin, "GET", `/api/exports?media_item_id=${retainedStatus.media_item_id}&media_source_id=${retainedStatus.media_source_id}`);
    assert.ok([ids.memberEdl, ids.ownerEdl, ids.defaultMcf].every(id => listed.publications.some(row => row.id === id)));
    const policyUrl = `/api/settings/filters/users/${created.id}`;
    const policy = await api(admin, "GET", policyUrl);
    const changed = await api(admin, "PUT", `${policyUrl}/policy`, { policy_revision: policy.policy_revision, rules: [], severities: ["violence"], ...fresh });
    assert.equal(changed.policy_revision, policy.policy_revision + 1);
    const observations = [];
    for (const id of [ids.memberEdl, ids.ownerEdl]) {
      await expect.poll(async () => {
        const status = await regenerationStatus(admin, id);
        if (status === null) return false;
        observations.push({ id, state: status.regeneration_state, serving: status.serving, policyRevision: status.policy_revision });
        return status.regeneration_state === "succeeded" && status.policy_revision === changed.policy_revision;
      }, { timeout: 30000, intervals: [100, 200, 500] }).toBe(true);
    }
    const regenerated = inventory(run);
    for (const id of [ids.memberEdl, ids.ownerEdl]) {
      assert.equal(regenerated[id].review_id, null);
      assert.equal(regenerated[id].policy_owner_id, created.id);
      assert.equal(regenerated[id].policy_revision, changed.policy_revision);
      assert.equal(regenerated[id].diskSha256, regenerated[id].contentSha256);
    }
    assert.equal(regenerated[ids.defaultMcf].diskSha256, before[ids.defaultMcf].diskSha256);
    assert.equal(digest(fs.readFileSync(media.file)), media.sha256);
    assert.deepEqual(query(run, "PRAGMA foreign_key_check"), []);
    run.write("review-deletion-result.json", {
      passed: true, feature: "MU-36", member: created.id, ids, staleExportRefusal: 409, smuggledHiddenRemoval: 422,
      result, before, after, regenerated, audit: events, observations, mediaUnchanged: true,
      scope: "Real authenticated source app API: member and owner publications from one member review, member preview hides owner and default files, stale preview after a concurrent republish, smuggled hidden removal refused, retain/remove with independent SQL and disk digests, export.retain/export.delete audit rows, retained discovery, and scheduler regeneration of retained files under the deleted review's owner. Fault injection, hard-exit recovery, and permission overrides use deterministic pytest evidence.",
    });
  } finally {
    run.write("review-deletion-http-statuses.json", requests);
    for (const context of contexts) await context.dispose();
  }
}
