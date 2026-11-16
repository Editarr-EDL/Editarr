import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { createRequire } from "node:module";
import path from "node:path";
import zlib from "node:zlib";
import { SIGN_IN_PATH, addJellyfinUser } from "./run-jellyfin.mjs";

/** A valid 2x2 RGBA PNG built in memory, so the journey needs no image library. */
function png() {
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type), data]);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const check = Buffer.alloc(4);
    check.writeUInt32BE(zlib.crc32(body));
    return Buffer.concat([length, body, check]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(2, 0);
  header.writeUInt32BE(2, 4);
  header.set([8, 6, 0, 0, 0], 8);
  const rows = Buffer.from([0, 255, 0, 0, 255, 0, 0, 255, 255, 0, 255, 0, 0, 255, 255, 255, 255, 255]);
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", zlib.deflateSync(rows)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/**
 * Drive the MU-15 Users API through the real server with accounts admitted by
 * Jellyfin sign-in on the run's Jellyfin, and prove the local-password routes are gone.
 * Needs a verification-mode launch and the owner created by `authenticate`.
 */
export async function verifyUsers(run, owner) {
  const { root, manifest } = run;
  const { request } = createRequire(path.join(root, "web/package.json"))("@playwright/test");
  const origin = manifest.origin;
  const steps = [];
  const contexts = [];
  const step = (name, status) => { steps.push({ name, status }); run.write("users-steps.json", steps); };
  const secret = () => { const value = randomBytes(24).toString("base64url"); run.secrets.add(value); return value; };

  async function actor() {
    const context = await request.newContext({ baseURL: origin, extraHTTPHeaders: { Origin: origin } });
    contexts.push(context);
    let csrf = null;
    const send = async (method, url, options = {}) => {
      const headers = { ...(csrf && method !== "GET" ? { "X-CSRF-Token": csrf } : {}), ...(options.headers ?? {}) };
      return context.fetch(url, { method, ...options, headers });
    };
    return {
      send,
      async signIn(username, password) {
        const response = await send("POST", SIGN_IN_PATH, { data: { username, password } });
        const body = await response.json();
        if (body.csrf_token) { run.secrets.add(body.csrf_token); csrf = body.csrf_token; }
        for (const cookie of await context.storageState().then(state => state.cookies)) run.secrets.add(cookie.value);
        return { status: response.status(), body };
      },
    };
  }
  const json = async response => ({ status: response.status(), body: await response.json() });

  try {
    const admin = await actor();
    assert.equal((await admin.signIn(owner.username, owner.password)).status, 200);
    const fresh = { reauthentication: { password: owner.password } };
    const removed = [];
    for (const [method, url, data] of [
      ["POST", "/api/users", { username: "verification-member", temporary_password: secret(), timezone: "UTC" }],
      ["POST", "/api/auth/login", { username: owner.username, password: owner.password }],
      ["POST", "/api/auth/register", { username: "self-registered", password: secret() }],
      ["PUT", "/api/auth/account", { current_password: owner.password, password: secret() }],
      ["GET", "/api/settings/registration"],
      ["PUT", "/api/settings/registration", { mode: "open" }],
      ["POST", "/api/users/1/password-reset", { temporary_password: secret(), ...fresh }],
    ]) {
      const response = await admin.send(method, url, data ? { data } : {});
      removed.push({ method, path: url, status: response.status() });
    }
    assert.ok(removed.every(entry => [404, 405].includes(entry.status)), `removed local-password routes answer 404 or 405: ${JSON.stringify(removed)}`);
    step("local account creation, local sign-in, registration, password change, and password reset routes are gone", 404);

    const jellyfinMember = await addJellyfinUser(run, "verification-member");
    const signedIn = await actor();
    const first = await signedIn.signIn(jellyfinMember.name, jellyfinMember.password);
    assert.equal(first.status, 200);
    assert.equal(first.body.access.principal.role, "user");
    const memberId = first.body.access.principal.user_id;
    step("a new Jellyfin user's first Jellyfin sign-in creates an ordinary account", first.status);
    const listing = await json(await signedIn.send("GET", "/api/users"));
    assert.deepEqual([listing.status, listing.body.code], [403, "capability_denied"]);
    step("ordinary user cannot list the directory", listing.status);
    const probes = [];
    for (const target of [1, 987654321]) probes.push(await json(await signedIn.send("GET", `/api/users/${target}/permissions`)));
    assert.deepEqual(probes.map(probe => [probe.status, probe.body.code]), [[404, "not_found"], [404, "not_found"]]);
    step("ordinary user cannot tell which account IDs exist", probes[0].status);
    const profile = await json(await signedIn.send("PATCH", `/api/users/${memberId}`, { data: { timezone: "Europe/Paris" } }));
    assert.equal(profile.status, 200);
    const named = await json(await signedIn.send("PATCH", `/api/users/${memberId}`, { data: { display_name: "Verification Member" } }));
    assert.deepEqual([named.status, named.body.code], [409, "linked_profile_field"]);
    const avatar = await json(await signedIn.send("PUT", `/api/users/${memberId}/avatar`, { data: png(), headers: { "Content-Type": "image/png" } }));
    assert.deepEqual([avatar.status, avatar.body.code], [409, "linked_profile_field"]);
    step("user edits own timezone; Jellyfin keeps the display name and avatar", profile.status);

    const noPassword = await json(await admin.send("PUT", `/api/users/${memberId}/role`, { data: { role: "admin" } }));
    assert.deepEqual([noPassword.status, noPassword.body.code], [403, "reauthentication_required"]);
    const promoted = await json(await admin.send("PUT", `/api/users/${memberId}/role`, { data: { role: "admin", ...fresh } }));
    assert.equal(promoted.body.role, "admin");
    const promotedList = await signedIn.send("GET", "/api/users");
    assert.equal(promotedList.status(), 200);
    step("owner role change applies to the member's next request", promotedList.status());
    const scope = await json(await signedIn.send("PUT", "/api/media-access/default", { data: { sources: [{ kind: "jellyfin_library", id: "b".repeat(32) }] } }));
    assert.deepEqual([scope.status, scope.body.code], [403, "reauthentication_required"]);

    const reader = await actor();
    await reader.signIn(owner.username, owner.password);
    const persisted = await json(await reader.send("GET", "/api/users"));
    const rows = Object.fromEntries(persisted.body.items.map(item => [item.id, item]));
    assert.equal(persisted.body.total, 2 + Object.keys(run.legacyMembers?.accounts ?? {}).length, "the owner, the member, and any seeded legacy accounts");
    assert.deepEqual([rows[memberId].role, rows[memberId].source, rows[memberId].status, rows[memberId].display_name], ["admin", "jellyfin", "active", "verification-member"]);
    step("fresh owner context reads every change back", persisted.status);
    run.write("users-result.json", {
      feature: "mu-users-roles", passed: true, removedRoutes: removed, accounts: persisted.body.items.map(item => ({
        id: item.id, role: item.role, source: item.source, status: item.status,
        hasAvatar: item.avatar !== null, signedIn: item.last_login_at !== null,
      })),
      scope: "Real verification-mode source app over HTTP; accounts come from Jellyfin sign-in on the run's Jellyfin 12; no UI exists until MU-25.",
    });

    const transferSteps = [];
    const transferStep = (name, status) => { transferSteps.push({ name, status }); run.write("owner-transfer-steps.json", transferSteps); };
    const ownerId = (await json(await admin.send("GET", "/api/auth/status"))).body.access.principal.user_id;
    const recipient = run.jellyfin.member;
    const successor = await actor();
    assert.equal((await successor.signIn(recipient.name, recipient.password)).status, 200);
    const successorId = (await json(await successor.send("GET", "/api/auth/status"))).body.access.principal.user_id;
    const successorFresh = { reauthentication: { password: recipient.password } };
    const byAdmin = await json(await signedIn.send("POST", `/api/users/${ownerId}/ownership`, { data: { reauthentication: { password: jellyfinMember.password } } }));
    assert.deepEqual([byAdmin.status, byAdmin.body.code], [403, "capability_denied"]);
    const unproven = await json(await admin.send("POST", `/api/users/${successorId}/ownership`, { data: {} }));
    assert.deepEqual([unproven.status, unproven.body.code], [403, "reauthentication_required"]);
    const wrong = await json(await admin.send("POST", `/api/users/${successorId}/ownership`, { data: { reauthentication: { password: secret() } } }));
    assert.deepEqual([wrong.status, wrong.body.code], [403, "reauthentication_failed"]);
    transferStep("an admin, an owner without its Jellyfin password, and a wrong Jellyfin password are refused", wrong.status);
    const moved = await json(await admin.send("POST", `/api/users/${successorId}/ownership`, { data: fresh }));
    assert.deepEqual(moved.body, { owner_id: successorId, former_owner_id: ownerId, former_owner_role: "admin" });
    transferStep("the Jellyfin owner confirms with its Jellyfin password and hands ownership to a Jellyfin-linked member", moved.status);
    const formerPower = await json(await admin.send("PUT", `/api/users/${memberId}/role`, { data: { role: "user", ...fresh } }));
    assert.equal(formerPower.status, 403, `the former owner cannot grant roles: ${formerPower.body.code}`);
    const newPower = await json(await successor.send("PUT", `/api/users/${memberId}/role`, { data: { role: "user", ...successorFresh } }));
    assert.equal(newPower.body.role, "user");
    transferStep("only the new owner holds owner powers on its next request", newPower.status);
    const back = await json(await successor.send("POST", `/api/users/${ownerId}/ownership`, { data: successorFresh }));
    assert.deepEqual(back.body, { owner_id: ownerId, former_owner_id: successorId, former_owner_role: "admin" });
    transferStep("the Jellyfin-linked owner confirms with its own Jellyfin password and hands ownership back", back.status);
    const transferReader = await actor();
    await transferReader.signIn(owner.username, owner.password);
    const roles = await json(await transferReader.send("GET", "/api/users"));
    const byId = Object.fromEntries(roles.body.items.map(item => [item.id, item]));
    assert.deepEqual([byId[ownerId].role, byId[successorId].role, byId[successorId].source], ["owner", "admin", "jellyfin"]);
    assert.deepEqual(roles.body.items.filter(item => item.role === "owner").map(item => item.id), [ownerId]);
    transferStep("a fresh Jellyfin sign-in reads exactly one owner after the round trip", roles.status);
    run.write("owner-transfer-result.json", {
      feature: "mu-owner-transfer", passed: true, owner: ownerId, recipient: successorId,
      scope: "Real verification-mode source app over HTTP; the owner and the recipient are both linked to the run's Jellyfin 12 and have no Editarr password.",
    });
  } finally {
    for (const context of contexts) await context.dispose();
  }
}
