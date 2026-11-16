import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { synthesizeMedia } from "./fixtures.mjs";
import { addJellyfinUser, adoptJellyfin } from "./run-jellyfin.mjs";
import { query } from "./database.mjs";
import { FORMATS, actors, background, digest, doctorPlugin, exportedCues, fileDigest, launchPlugin, layout, mcf, mediaTree, pluginCheckout, pluginMatrix, withPluginRun, withdrawalOrder } from "./plugin-interop-support.mjs";

const LEGACY = { edl: "3 3.5 0\n", mcf: mcf("Legacy adjacent file", 3) };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/**
 * Drive Editarr publication through first personal publication, policy ownership, regeneration,
 * copy, transfer, and retention while the public plugin, loaded in a disposable
 * Jellyfin 12.0.0 server over the same media tree, serves each user's file. Every
 * expectation is the published bytes on disk; the plugin matrix compares served bytes.
 */
export async function verifyPluginInterop(run, setUp) {
  const plugin = pluginCheckout(run.root);
  const source = synthesizeMedia(run.root, run.scratch, run.data);
  const { tree, files } = mediaTree(run, source.file);
  const legacy = Object.fromEntries(FORMATS.map(format => {
    const file = layout(files.movie, format).legacy;
    fs.writeFileSync(file, LEGACY[format]);
    return [format, { file, sha256: fileDigest(file) }];
  }));
  const mediaBefore = Object.fromEntries(Object.entries(files).map(([key, file]) => [key, fileDigest(file)]));
  const api = actors(run);
  const phases = {};
  const publications = {};
  let launched;
  await withPluginRun(plugin, run, async () => {
    launched = await launchPlugin(plugin, run, tree);
    await doctorPlugin(plugin, run, launched);
    const { port, image } = launched.record;
    await adoptJellyfin(run, { base: `http://127.0.0.1:${port}`, image, port, admin: { name: "jfedl-admin", password: launched.hidden.password } });
    const owner = await setUp();
    const jf = launched.users;
    const admin = await api.signIn(owner.username, owner.password);
    const fresh = password => ({ reauthentication: { password } });
    const ownerId = (await admin("GET", "/api/auth/status")).access.principal.user_id;
    const alice = await api.member("jfedl-userA", launched.hidden.userAPassword);
    const bob = await api.member("jfedl-userB", launched.hidden.userBPassword);
    const carolUser = await addJellyfinUser(run, "interop-carol");
    const carol = await api.member(carolUser.name, carolUser.password);
    const daveUser = await addJellyfinUser(run, "interop-dave");
    const dave = await api.member(daveUser.name, daveUser.password);

    async function importReview(who, key, name, start, drugsAt) {
      const indexed = launched.items[files[key]];
      assert.ok(indexed, `Jellyfin indexed ${files[key]}`);
      const target = { source: "jellyfin", item_id: indexed.item, media_source_id: indexed.source };
      const { review } = await who("POST", "/api/mcf-imports/upload", null, 201, {
        options: JSON.stringify({ target, name, synchronization: { start_seconds: 0, end_seconds: source.durationSeconds, end_mode: "file_end" }, mcf_material_end_seconds: 4 }),
        file: { name: `${name}.mcf`, mimeType: "text/vtt", buffer: Buffer.from(mcf(name, start, drugsAt)) },
      });
      return review;
    }
    /** Publish both formats of `reviewId` as `who`, confirming a replacement; return the canonical records. */
    async function publish(who, reviewId, target, label) {
      const review = await who("GET", `/api/reviews/${reviewId}`);
      const result = {};
      for (const format of FORMATS) {
        const url = `/api/reviews/${reviewId}/exports/${format}`;
        const body = { revision: review.revision, policy_owner_id: review.policy_owner_id, policy_revision: review.policy_revision, target };
        let answer = await who("POST", url, body, null);
        if (answer.status === 409 && answer.body.detail?.code === "export_confirmation_required") answer = await who("POST", url, { ...body, confirmation: answer.body.detail.confirmation }, null);
        assert.equal(answer.status, 200, `${label} ${format}: ${JSON.stringify(answer.body)}`);
        result[format] = await published(who, answer.body.publication?.id ?? answer.body.id);
      }
      publications[label] = result;
      return result;
    }
    /** A publication's canonical record and its file at the contract path, whose bytes must match the record. */
    async function published(who, exportId) {
      const record = await who("GET", `/api/exports/${exportId}`);
      const [row] = query(run, "SELECT target_export_id, destination_path FROM media_item_exports WHERE id = ?", exportId);
      const media = Object.values(files).find(file => path.dirname(row.destination_path).startsWith(path.dirname(file)));
      const expected = layout(media, record.format, record.scope === "personal" ? row.target_export_id : null);
      assert.equal(row.destination_path, record.scope === "personal" ? expected.personal : expected.default);
      const sha256 = fs.existsSync(row.destination_path) ? fileDigest(row.destination_path) : null;
      assert.equal(sha256, record.content_sha256, `export ${exportId} bytes on disk match its record`);
      return { id: exportId, file: row.destination_path, sha256, exportId: row.target_export_id, record: pick(record) };
    }
    const reread = async (who, entry) => published(who, entry.id);
    const expectFile = (name, principal, format, key, sha256, extra = {}) => ({ name, principal, format, media: files[key], status: 200, sha256: [sha256], ...extra });
    const expectStatus = (name, principal, format, key, status, extra = {}) => ({ name, principal, format, media: files[key], status, ...extra });
    const matrix = async (label, cases) => (phases[label] = summarize(await pluginMatrix(plugin, run, launched, label, cases)));

    const instance = await admin("GET", "/api/settings/filters");
    const ruleId = randomUUID();
    const drugsRule = randomUUID();
    await admin("PUT", "/api/settings/filters", { ...instance, selection_rules: [...instance.selection_rules, { id: ruleId, disposition: "deny", category: "violence" }, { id: drugsRule, disposition: "deny", category: "drugs" }], severity_defaults: instance.severity_defaults.map(value => ["violence", "drugs"].includes(value.subcategory) ? { ...value, severity: "high" } : value), ...fresh(owner.password) });
    const enforcePolicy = async (account, rules, severities) => {
      const policy = `/api/settings/filters/users/${account.id}`;
      return admin("PUT", `${policy}/policy`, { policy_revision: (await admin("GET", policy)).policy_revision, rules, severities, ...fresh(owner.password) });
    };
    const enforce = rules => enforcePolicy(dave, rules, rules.length ? ["violence"] : []);

    const defaults = {};
    for (const key of ["movie", "policy", "hidden"]) defaults[key] = await publish(admin, (await importReview(admin, key, `Owner default ${key}`, 1)).id, "item_default", `default-${key}`);
    for (const key of ["v1080", "transfer"]) await importReview(admin, key, `Owner ${key}`, 1);
    const reviews = {
      aliceMovie: await importReview(alice.call, "movie", "Alice movie", 2),
      aliceVersion: await importReview(alice.call, "v1080", "Alice 1080p", 2),
      bobMovie: await importReview(bob.call, "movie", "Bob movie", 3),
    };
    await matrix("before-personal", FORMATS.flatMap(format => [
      expectFile(`${format}-a-default-before-personal`, "userA", format, "movie", defaults.movie[format].sha256),
      expectFile(`${format}-b-default-before-personal`, "userB", format, "movie", defaults.movie[format].sha256),
      expectFile(`${format}-key-default`, "apiKey", format, "movie", defaults.movie[format].sha256),
      expectStatus(`${format}-anonymous`, "anonymous", format, "movie", 401),
      expectStatus(`${format}-a-version-none`, "userA", format, "v1080", 404, { version: files.v1080 }),
    ]));

    const publishing = ["userA", "userB"].flatMap(user => FORMATS.map(format => background(pluginMatrix(plugin, run, launched, `publishing-${user.toLowerCase()}-${format}`, [{
      name: `${format}-${user}-default-until-personal`, principal: user, format, media: files.movie, status: 200, sha256: [defaults.movie[format].sha256], untilChanged: true, seconds: 120,
    }]))));
    await sleep(1500);
    const linked = Object.fromEntries([["jfedl-admin", ownerId], ["jfedl-userA", alice.id], ["jfedl-userB", bob.id]].map(([name, id]) => [name, query(run, "SELECT server_id, provider_user_id FROM user_provider_identities WHERE user_id = ?", id)[0]]));
    assert.equal(linked["jfedl-admin"].provider_user_id, jf.admin, "setup linked the owner to the plugin Jellyfin's administrator");
    const moved = {
      aliceMovie: await publish(alice.call, reviews.aliceMovie.id, "self", "alice-movie-personal"),
      aliceVersion: await publish(alice.call, reviews.aliceVersion.id, "self", "alice-1080p-personal"),
      bobMovie: await publish(bob.call, reviews.bobMovie.id, "self", "bob-movie-personal"),
    };
    for (const [key, user] of [["aliceMovie", "userA"], ["aliceVersion", "userA"], ["bobMovie", "userB"]]) {
      for (const format of FORMATS) assert.equal(moved[key][format].exportId, jf[user], `${key} ${format} is published under the Jellyfin export ID`);
    }
    const switched = (await Promise.all(publishing)).flat();
    for (const [key, user] of [["aliceMovie", "userA"], ["bobMovie", "userB"]]) {
      for (const format of FORMATS) {
        const check = switched.find(item => item.check === `${format}-${user}-default-until-personal`);
        moved[key][format].publishPoll = { ok: check.ok, answers: check.answers, digestsSeen: check.digestsSeen, statusesSeen: check.statusesSeen };
        assert.ok(check.ok, `${user} ${format} publish poll: ${JSON.stringify(moved[key][format].publishPoll)}`);
        assert.equal(check.bodySha256, moved[key][format].sha256, "publishing switched the plugin from the default straight to the personal file");
      }
    }
    phases.publishing = { checks: switched.map(check => ({ check: check.check, status: check.status, sha256: check.bodySha256, answers: check.answers, digestsSeen: check.digestsSeen })), digests: [...new Set(switched.flatMap(check => check.digestsSeen))] };
    await matrix("after-personal", FORMATS.flatMap(format => [
      expectFile(`${format}-a-own`, "userA", format, "movie", moved.aliceMovie[format].sha256),
      expectFile(`${format}-b-own`, "userB", format, "movie", moved.bobMovie[format].sha256),
      expectFile(`${format}-b-with-a-etag`, "userB", format, "movie", moved.bobMovie[format].sha256, { ifNoneMatch: `${format}-a-own` }),
      expectStatus(`${format}-a-own-etag`, "userA", format, "movie", 304, { ifNoneMatch: `${format}-a-own` }),
      expectFile(`${format}-key-with-a-etag`, "apiKey", format, "movie", defaults.movie[format].sha256, { ifNoneMatch: `${format}-a-own` }),
      expectFile(`${format}-admin-default`, "admin", format, "movie", defaults.movie[format].sha256),
      expectFile(`${format}-a-1080p`, "userA", format, "v1080", moved.aliceVersion[format].sha256, { version: files.v1080 }),
      expectStatus(`${format}-a-2160p-none`, "userA", format, "v1080", 404, { version: files.v2160 }),
      expectStatus(`${format}-a-foreign-version`, "userA", format, "v1080", 404, { version: files.movie }),
      expectStatus(`${format}-b-1080p-none`, "userB", format, "v1080", 404, { version: files.v1080 }),
      expectFile(`${format}-a-hidden-default`, "userA", format, "hidden", defaults.hidden[format].sha256),
      expectStatus(`${format}-b-hidden-invisible`, "userB", format, "hidden", 404),
    ]));

    const ownership = await verifyOwnership({ run, admin, alice, bob, carol, dave, ownerId, fresh, importReview, publish, reread, enforce, enforcePolicy, ruleId, drugsRule, files, defaults, matrix, expectFile, plugin, launched, owner, moved });
    const retention = await verifyRetention({ admin, alice, bob, fresh, owner, reread, moved, ownership, matrix, expectFile, files });
    const errors = await verifyErrors({ launched, moved, matrix, expectFile, expectStatus, defaults });

    const served = Object.values(phases).flatMap(phase => phase.digests);
    for (const format of FORMATS) {
      assert.equal(fileDigest(legacy[format].file), legacy[format].sha256, "legacy adjacent file unchanged");
      assert.equal(served.includes(legacy[format].sha256), false, "legacy adjacent file never served");
    }
    for (const [key, file] of Object.entries(files)) assert.equal(fileDigest(file), mediaBefore[key], `${key} media bytes unchanged`);
    run.write("plugin-interop-result.json", {
      passed: true,
      editarr: { commit: git(run.root, "rev-parse", "HEAD"), dirtyPaths: git(run.root, "status", "--porcelain").split("\n").filter(Boolean).length },
      plugin: { checkout: plugin.checkout, commit: launched.record.commit, dirtyPaths: launched.record.dirtyPaths, runId: launched.record.runId, evidence: launched.evidence, image: launched.record.image, pluginSha256: launched.record.pluginSha256 },
      fixtures: { syntheticMedia: { fixture: source.fixture, sha256: source.sha256 }, exportLayoutManifestSha256: manifestDigest(plugin.checkout), legacy: Object.fromEntries(FORMATS.map(format => [format, legacy[format].sha256])) },
      jellyfin: { users: jf, linked }, phases, publications, ownership: ownership.summary, retention, errors,
      scope: "Live public plugin in a disposable Jellyfin 12.0.0 container over Editarr's synthetic media tree, mounted at the same path. Editarr's real HTTP API published, copied, transferred, regenerated, withdrew, and deleted; every account signed in with Jellyfin; the plugin matrix requested both routes as two linked users, the linked owner, a server API key, and no token, and compared served digests with the published files.",
    });
    fs.writeFileSync(path.join(launched.evidence, "counterpart.json"), `${JSON.stringify({ editarrCommit: git(run.root, "rev-parse", "HEAD"), editarrEvidence: run.evidence }, null, 2)}\n`);
    console.log("PASS: plugin served each Jellyfin user's exact EDL and MCF bytes through first publication, export-to-self, copy, transfer, regeneration, withdrawal, review and account deletion; copy and transfer followed the new owner's policy; withdrawal served no default before the policy change or while Editarr still reported the personal file serving; malformed personal files never fell back; legacy files unchanged and unused.");
  }, async () => {
    run.write("plugin-interop-http-statuses.json", api.statuses);
    await api.close();
  });
}

/** Admin export-to-self, regeneration with the old file served throughout, failure withdrawal, copy, and transfer. */
async function verifyOwnership(context) {
  const { run, admin, alice, bob, carol, dave, ownerId, fresh, importReview, publish, reread, enforce, enforcePolicy, ruleId, drugsRule, files, defaults, matrix, expectFile, plugin, launched, owner, moved } = context;
  const fields = ["revision", "name", "memberships", "mcf_document", "synchronization", "accepted_credits", "policy_owner_id", "policy_revision"];
  const unselect = (who, review, category) => who("PUT", `/api/reviews/${review.id}`, {
    ...Object.fromEntries(fields.map(key => [key, review[key]])), filter_basis: "base",
    filters: review.base_filters.map(value => ({ ...value, selected: category ? value.category !== category && value.selected : false })),
  });
  const policyReview = await importReview(dave.call, "policy", "Dave policy", 2);
  const unselected = await unselect(dave.call, policyReview);
  assert.equal(unselected.filters[0].selected, false, "the unenforced base leaves the cue out of the EDL");
  await enforce([ruleId]);
  const toSelf = await publish(admin, policyReview.id, "self", "owner-self-from-dave");
  for (const entry of Object.values(toSelf)) {
    assert.deepEqual([entry.record.policy_owner_id, entry.record.target_user_id, entry.record.actor_id, entry.record.target], [dave.id, ownerId, ownerId, "self"]);
  }
  await matrix("export-to-self", FORMATS.flatMap(format => [
    expectFile(`${format}-admin-own-from-dave`, "admin", format, "policy", toSelf[format].sha256),
    expectFile(`${format}-b-default`, "userB", format, "policy", defaults.policy[format].sha256),
  ]));

  const regeneration = { started: Date.now() };
  const polling = FORMATS.map(format => background(pluginMatrix(plugin, run, launched, `regeneration-${format}`, [{
    name: `${format}-admin-old-until-changed`, principal: "admin", format, media: files.policy, status: 200, sha256: [toSelf[format].sha256], untilChanged: true, seconds: 60,
  }])));
  await sleep(1500);
  await enforce([]);
  const regenerated = {};
  for (const format of FORMATS) regenerated[format] = await settle(admin, toSelf[format].id, "succeeded");
  const changed = (await Promise.all(polling)).flat();
  for (const format of FORMATS) {
    const entry = await reread(admin, toSelf[format]);
    const check = changed.find(item => item.check.startsWith(format));
    regenerated[format] = { ...regenerated[format], before: toSelf[format].sha256, after: entry.sha256, plugin: { ok: check.ok, answers: check.answers, digestsSeen: check.digestsSeen, statusesSeen: check.statusesSeen } };
    run.write("plugin-interop-regeneration.json", regenerated);
    assert.ok(check.ok, `${format} regeneration poll: ${JSON.stringify(regenerated[format])}`);
    assert.notEqual(entry.sha256, toSelf[format].sha256, "regeneration changed the bytes");
    assert.equal(check.bodySha256, entry.sha256, "plugin moved straight from the old file to the regenerated file");
    assert.deepEqual(check.digestsSeen, [toSelf[format].sha256, entry.sha256].sort());
  }

  const away = `${files.policy}.away`;
  fs.renameSync(files.policy, away);
  const withdrawal = { formats: {} };
  try {
    const watching = FORMATS.map(format => background(pluginMatrix(plugin, run, launched, `withdrawal-${format}`, [{
      name: `${format}-admin-personal-until-default`, principal: "admin", format, media: files.policy, status: 200, sha256: [regenerated[format].after], untilChanged: true, seconds: 120,
    }])));
    await sleep(1500);
    withdrawal.enforceSentAt = Date.now();
    await enforce([ruleId]);
    withdrawal.enforceReceivedAt = Date.now();
    const states = await Promise.all(FORMATS.map(format => settle(admin, toSelf[format].id, "failed", 50)));
    const watched = (await Promise.all(watching)).flat();
    FORMATS.forEach((format, index) => {
      const editarr = states[index].seen;
      const plugin = watched.find(check => check.check.startsWith(format)).timeline;
      withdrawal.formats[format] = { editarr, plugin };
      run.write("plugin-interop-withdrawal.json", withdrawal);
      assert.deepEqual(withdrawalOrder({ enforceSentAt: withdrawal.enforceSentAt, editarr, plugin, personalSha256: regenerated[format].after, defaultSha256: defaults.policy[format].sha256 }), [], `${format} withdrawal order`);
      assert.equal(fs.existsSync(toSelf[format].file), false, "withdrawal removed the personal file");
    });
    await matrix("withdrawn", FORMATS.map(format => expectFile(`${format}-admin-default-after-withdrawal`, "admin", format, "policy", defaults.policy[format].sha256)));
  } finally { fs.renameSync(away, files.policy); }

  const copySource = await importReview(bob.call, "movie", "Bob copy source", 0, 2);
  await unselect(bob.call, copySource, "drugs");
  await enforcePolicy(bob, [drugsRule], ["drugs"]);
  await quiesce(admin, Object.values(moved.bobMovie).map(entry => entry.id));
  const detail = await admin("GET", `/api/reviews/${copySource.id}`);
  const copy = await admin("POST", `/api/reviews/${copySource.id}/duplicate`, { revision: detail.revision, policy_owner_id: detail.policy_owner_id, policy_revision: detail.policy_revision, name: "Owner copy of Bob copy source" }, 201);
  const copied = await publish(admin, copy.id, "self", "owner-self-from-copy");
  for (const entry of Object.values(copied)) assert.deepEqual([entry.record.policy_owner_id, entry.record.target_user_id, entry.record.original_author_id], [ownerId, ownerId, bob.id]);
  const policies = { copy: { originalAuthor: { user: bob.id, enforced: { rules: ["deny drugs"], severities: { drugs: "high" } } }, newOwner: { user: ownerId, enforced: null, why: "the owner's policy cannot be enforced" } } };
  expectCues(copied, { edl: ["violence"], mcf: ["violence=low"] }, "copy follows the owner's empty policy, not Bob's drugs floor");
  for (const format of FORMATS) assert.notEqual(copied[format].sha256, defaults.movie[format].sha256, `${format}: the copy's bytes differ from the item default the admin would otherwise get`);
  await matrix("copy", FORMATS.map(format => expectFile(`${format}-admin-copy`, "admin", format, "movie", copied[format].sha256)));

  const carolSource = await importReview(carol.call, "transfer", "Carol transfer", 1, 3);
  await unselect(carol.call, carolSource, "drugs");
  await enforcePolicy(carol, [], ["violence"]);
  await enforcePolicy(alice, [drugsRule], ["drugs"]);
  await quiesce(admin, [...Object.values(moved.aliceMovie), ...Object.values(moved.aliceVersion)].map(entry => entry.id));
  policies.transfer = { originalAuthor: { user: carol.id, enforced: { rules: [], severities: { violence: "high" } } }, newOwner: { user: alice.id, enforced: { rules: ["deny drugs"], severities: { drugs: "high" } } } };
  const preview = await admin("GET", `/api/users/${carol.id}/deletion?mode=transfer&destination=${alice.id}`);
  await admin("POST", `/api/users/${carol.id}/deletion`, { mode: "transfer", confirmation: preview.confirmation, files: [], destination_id: alice.id, ...fresh(owner.password) });
  const carolReview = query(run, "SELECT id FROM saved_reviews WHERE name = 'Carol transfer'")[0].id;
  const transferred = await publish(alice.call, carolReview, "self", "alice-self-from-transfer");
  for (const entry of Object.values(transferred)) assert.deepEqual([entry.record.policy_owner_id, entry.record.target_user_id, entry.record.original_author_id], [alice.id, alice.id, carol.id]);
  expectCues(transferred, { edl: ["violence", "drugs"], mcf: ["violence=low", "drugs=high"] }, "transfer follows Alice's drugs floor and severity, not Carol's violence severity");
  await matrix("transfer", FORMATS.map(format => expectFile(`${format}-a-transferred`, "userA", format, "transfer", transferred[format].sha256)));
  return { copied, transferred, summary: { exportToSelf: toSelf, regenerated, withdrawal, policies, copied, transferred } };
}

/** Assert each format's exported cue selection and severities before the plugin compares bytes. */
function expectCues(published, expected, message) {
  for (const format of FORMATS) {
    const cues = exportedCues(format, fs.readFileSync(published[format].file));
    published[format].cues = cues;
    assert.deepEqual(cues, expected[format], `${format}: ${message}`);
  }
}

/** Wait until none of these publications is regenerating after a policy change; a mid-regeneration read may be stale. */
async function quiesce(call, exportIds) {
  const deadline = Date.now() + 90000;
  for (const exportId of exportIds) {
    for (;;) {
      const answer = await call("GET", `/api/exports/${exportId}`, null, null);
      if (answer.status === 200 && !answer.body.regenerating) break;
      if (answer.status !== 200) assert.deepEqual([answer.status, answer.body.detail?.code], [409, "stale_export_revision"]);
      if (Date.now() > deadline) throw new Error(`export ${exportId} kept regenerating`);
      await sleep(250);
    }
  }
}

/** Retained files keep serving after review deletion and account deletion. */
async function verifyRetention({ admin, alice, bob, fresh, owner, reread, moved, ownership, matrix, expectFile, files }) {
  const bobMovie = moved.bobMovie.edl.record.review_id;
  const preview = await bob.call("GET", `/api/reviews/${bobMovie}/deletion`);
  const deletion = await bob.call("POST", `/api/reviews/${bobMovie}/deletion`, { revision: preview.revision, confirmation: preview.confirmation, files: preview.files.map(file => ({ export_id: file.publication.id, choice: "retain" })) });
  const bobRetained = {};
  for (const format of FORMATS) {
    bobRetained[format] = await reread(admin, moved.bobMovie[format]);
    assert.equal(bobRetained[format].sha256, moved.bobMovie[format].sha256, "review deletion retained the file");
  }
  await matrix("review-deleted", FORMATS.map(format => expectFile(`${format}-b-retained-after-review-deletion`, "userB", format, "movie", moved.bobMovie[format].sha256)));

  const accountPreview = await admin("GET", `/api/users/${alice.id}/deletion?mode=retain`);
  await admin("POST", `/api/users/${alice.id}/deletion`, { mode: "retain", confirmation: accountPreview.confirmation, files: [], ...fresh(owner.password) });
  await matrix("account-deleted", FORMATS.flatMap(format => [
    expectFile(`${format}-a-movie-retained`, "userA", format, "movie", moved.aliceMovie[format].sha256),
    expectFile(`${format}-a-1080p-retained`, "userA", format, "v1080", moved.aliceVersion[format].sha256, { version: files.v1080 }),
    expectFile(`${format}-a-transfer-retained`, "userA", format, "transfer", ownership.transferred[format].sha256),
  ]));
  return { reviewDeletion: { reviewId: bobMovie, result: deletion, files: bobRetained }, accountDeletion: { userId: alice.id, mode: "retain" } };
}

/** Malformed or unreadable personal files return errors and never the default; restoring them restores service. */
async function verifyErrors({ launched, moved, matrix, expectFile, expectStatus, defaults }) {
  const edl = moved.bobMovie.edl.file;
  const mcfFile = moved.aliceMovie.mcf.file;
  const unreadable = moved.bobMovie.mcf.file;
  const saved = [edl, mcfFile].map(file => [file, fs.readFileSync(file)]);
  const reads = {};
  try {
    fs.writeFileSync(edl, "not an edl\n");
    fs.writeFileSync(mcfFile, "WEBVTT MovieContentFilter 1.1.0\n\nbroken\n");
    fs.chmodSync(unreadable, 0o000);
    reads.serverReadsMode000 = spawnSync("docker", ["exec", launched.record.container, "cat", unreadable]).status === 0;
    await matrix("malformed", [
      expectStatus("edl-b-invalid-personal", "userB", "edl", "movie", 422),
      expectStatus("mcf-a-invalid-personal", "userA", "mcf", "movie", 422),
      reads.serverReadsMode000
        ? expectFile("mcf-b-mode-000-readable", "userB", "mcf", "movie", moved.bobMovie.mcf.sha256)
        : expectStatus("mcf-b-unreadable-personal", "userB", "mcf", "movie", 500),
      ...["edl", "mcf"].map(format => expectFile(`${format}-key-default-unaffected`, "apiKey", format, "movie", defaults.movie[format].sha256)),
    ]);
  } finally {
    for (const [file, content] of saved) fs.writeFileSync(file, content);
    fs.chmodSync(unreadable, 0o644);
  }
  await matrix("restored", FORMATS.flatMap(format => [
    expectFile(`${format}-a-restored`, "userA", format, "movie", moved.aliceMovie[format].sha256),
    expectFile(`${format}-b-restored`, "userB", format, "movie", moved.bobMovie[format].sha256),
  ]));
  return reads;
}

/** Poll a publication every `interval` ms until its regeneration reaches `state` (`succeeded`, or `failed` with serving false); `seen` holds each state span's first and last poll times. */
async function settle(call, exportId, state, interval = 250) {
  const deadline = Date.now() + 90000;
  const seen = [];
  while (Date.now() < deadline) {
    const sentAt = Date.now();
    const answer = await call("GET", `/api/exports/${exportId}`, null, null);
    const receivedAt = Date.now();
    if (answer.status === 200) {
      const { regeneration_state: current, serving, failure_reason: reason, regenerating } = answer.body;
      const last = seen.at(-1);
      if (last?.state === current && last?.serving === serving) Object.assign(last, { polls: last.polls + 1, lastSentAt: sentAt, lastReceivedAt: receivedAt });
      else seen.push({ state: current, serving, reason, regenerating, polls: 1, firstSentAt: sentAt, firstReceivedAt: receivedAt, lastSentAt: sentAt, lastReceivedAt: receivedAt });
      if (current === state && (state !== "failed" || serving === false)) return { exportId, seen, content_sha256: answer.body.content_sha256 };
    } else assert.deepEqual([answer.status, answer.body.detail?.code], [409, "stale_export_revision"]);
    await sleep(interval);
  }
  throw new Error(`export ${exportId} never reached ${state}: ${JSON.stringify(seen)}`);
}

function summarize(checks) {
  return { requests: checks.reduce((sum, check) => sum + (check.answers ?? 1), 0), digests: checks.flatMap(check => check.digestsSeen ?? [check.bodySha256]).filter((value, index, all) => all.indexOf(value) === index),
    checks: checks.map(check => ({ check: check.check, principal: check.case.principal, format: check.case.format, status: check.status, sha256: check.status === 200 ? check.bodySha256 : null, etag: Boolean(check.etag), answers: check.answers })) };
}

const pick = record => Object.fromEntries(["id", "publication_id", "publication_revision", "media_item_id", "format", "scope", "target", "target_user_id", "target_export_id", "actor_id", "policy_owner_id", "original_author_id", "policy_revision", "review_id", "content_sha256", "regeneration_state", "serving"].map(key => [key, record[key]]));
const git = (root, ...args) => execFileSync("git", ["-C", root, ...args], { encoding: "utf8" }).trim();
const manifestDigest = checkout => digest(fs.readFileSync(path.join(checkout, "tests/fixtures/export-layout-v1/manifest.json")));
